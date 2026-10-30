# Copyright © 2026 Aurélien "Akiway" Klein.
"""Update ArchiveXL sector node removals and mutations from one game version to another.

Usage:
    python axl_sector_updater.py --from 2.31 --to 2.5 [input] [output] [options]

input / output are an .xl file or a folder (default: the input/ and output/ folders next to this script).
--from / --to are game versions. Their node exports (nodes-<version>.zip: one <sector path>.jsonl per sector, one
record per nodeData entry, see docs/NODE_RECORDS.md) are read from the versions/ folder and downloaded from the GitHub
release when missing; a path to an export zip or unpacked folder also works.

Every removal and mutation is looked up by what the node *is* (node family, resources, world position, debugName,
actor/instance positions) instead of by index, then written back with its new index, type, expectedNodes and
remapped actor / instance lists. The output is YAML written into the input text, so its comments stay; nodes not
found are commented out with what they were in the source version. --format json rewrites the file as JSON instead.
With --debug, a report (<output>.report.tsv) lists how each node was matched so that anything uncertain can be
checked by hand.
"""
import argparse, csv, functools, json, math, os, re, sys, urllib.error, urllib.request, zipfile
from collections import defaultdict

import yaml

EXACT_DISTANCE = 0.01   # m: same placement
NEAR_DISTANCE = 0.05    # m: same placement after re-cooking (float noise, re-snapped transforms)
ELEMENT_DISTANCE = 0.01 # m: same actor / instance
SECTOR_RX = re.compile(r'^(?P<prefix>.*_)(?P<x>-?\d+)_(?P<y>-?\d+)_(?P<z>-?\d+)_(?P<lod>\d+)\.streamingsector$')
# Edge (m) of a level 0 sector per name prefix; it doubles at each level. Checked on 2.31 and 2.5 up to level 4
# (levels 5 and 6 do not follow it).
CELL_SIZES = {'exterior_': 64, 'interior_': 32}
GRID_LEVELS = range(5)
# Their actors / instances are stored relative to the node: they only locate a node that did not move.
RELATIVE_ELEMENTS = {'worldInstancedDestructibleMeshNode', 'worldInstancedOccluderNode'}
LEVELS = ['exact', 'match', 'moved']  # best first; then "not found" and "source mismatch"
STATUSES = LEVELS + ['not found', 'source mismatch']

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
VERSIONS_DIR = os.path.join(SCRIPT_DIR, 'versions')  # node exports, downloaded here when missing
INPUT_DIR = os.path.join(SCRIPT_DIR, 'input')        # default input
OUTPUT_DIR = os.path.join(SCRIPT_DIR, 'output')      # default output
RELEASE_URL = 'https://github.com/Akiway/ArchiveXL-Sector-Updater/releases/download/node-exports/'
NODES_SCHEMA = 1                                     # version of docs/NODE_RECORDS.md
NODES_MANIFEST = 'manifest.json'
XL_EXTENSIONS = ('.xl', '.yaml', '.yml', '.json')
VERSION_RX = re.compile(r'^\d+(\.\d+)*$')


def nodes_zip_name(version):
    return f'nodes-{version}.zip'


# ---------------------------------------------------------------- records

def fnv1a64(text):
    h = 0xcbf29ce484222325
    for b in text.encode('utf-8'):
        h = ((h ^ b) * 0x100000001b3) & 0xFFFFFFFFFFFFFFFF
    return h


@functools.lru_cache(maxsize=None)  # the same resources come back in thousands of records
def resource_key(prop, value):
    """Resources compared by hash: cooked 2.5 data stores some names as FNV1a64 hashes, paths may be unresolved."""
    if value.isdigit():
        return int(value)
    return fnv1a64(value if 'ppearance' in prop else value.lower())


class Record:
    __slots__ = ('index', 'type', 'family', 'debug_name', 'position', 'orientation', 'resources', 'elements', 'raw')

    def __init__(self, raw):
        self.raw = raw
        self.index = raw['i']
        self.type = raw['t']
        self.family = raw['t'].replace('worldCooked', 'world', 1)
        name = raw.get('d')
        self.debug_name = None if name in (None, '', 'None') else name
        self.position = raw['p']
        self.orientation = raw['q']
        self.resources = {resource_key(k, v) for k, v in (raw.get('r') or {}).items()}
        self.elements = raw.get('e')


class NodeExport:
    """Node export of one game version: a nodes-<version>.zip (see pack_nodes.py) or an unpacked folder."""

    def __init__(self, location):
        self.location = location
        self._cache = {}
        if os.path.isdir(location):
            self._zip = None
            manifest = os.path.join(location, NODES_MANIFEST)
            self.manifest = json.load(open(manifest, encoding='utf-8')) if os.path.exists(manifest) else {}
        else:
            self._zip = zipfile.ZipFile(location)
            self._names = {n.lower(): n for n in self._zip.namelist()}
            self.manifest = json.loads(self._zip.read(NODES_MANIFEST)) if NODES_MANIFEST in self._names else {}

    @property
    def version(self):
        return self.manifest.get('game_version') or os.path.basename(self.location)

    def _lines(self, path):
        if self._zip:
            name = self._names.get(path.replace('\\', '/').lower() + '.jsonl')
            return self._zip.read(name).decode('utf-8').splitlines() if name else None
        f = os.path.join(self.location, *path.split('\\')) + '.jsonl'
        return open(f, encoding='utf-8').read().splitlines() if os.path.exists(f) else None

    def sector(self, path):
        if path not in self._cache:
            lines = self._lines(path)
            self._cache[path] = None if lines is None else Sector([l for l in lines if l])
        return self._cache[path]


class Sector:
    """Records of one sector, decoded on first access: from the source version, a removal only needs its own node."""
    __slots__ = ('_lines', '_records', '_count')

    def __init__(self, lines):
        self._lines = lines
        self._records = [None] * len(lines)
        self._count = len(lines)

    def __len__(self):
        return self._count

    def __getitem__(self, i):
        record = self._records[i]
        if record is None:
            record = self._records[i] = Record(json.loads(self._lines[i]))
        return record

    def __iter__(self):
        if self._lines is not None:
            for i in range(self._count):
                self[i]
            self._lines = None  # all decoded
        return iter(self._records)


def download(url, dest):
    tmp = dest + '.part'
    print(f'downloading {url}', file=sys.stderr)
    with urllib.request.urlopen(url) as r, open(tmp, 'wb') as f:
        total = int(r.headers.get('Content-Length') or 0)
        done = 0
        while chunk := r.read(1 << 20):
            f.write(chunk)
            done += len(chunk)
            print(f'\r  {done / 2**20:.0f} / {total / 2**20:.0f} MiB', end='', file=sys.stderr)
    print(file=sys.stderr)
    if total and done != total:
        os.remove(tmp)
        raise SystemExit(f'download interrupted: {done} of {total} bytes')
    os.replace(tmp, dest)


def open_export(spec, versions_dir, release_url):
    """spec: a game version (<versions_dir>/nodes-<version>.zip, downloaded when missing), or a zip / unpacked folder."""
    if os.path.exists(spec):
        export = NodeExport(spec)
    elif VERSION_RX.match(spec):
        path = os.path.join(versions_dir, nodes_zip_name(spec))
        if not os.path.exists(path):
            os.makedirs(versions_dir, exist_ok=True)
            try:
                download(release_url + nodes_zip_name(spec), path)
            except urllib.error.HTTPError as e:
                local = sorted(n for n in os.listdir(versions_dir) if n.endswith('.zip'))
                raise SystemExit(f'no node export for game version {spec} (HTTP {e.code} from {release_url}); '
                                 f'in {versions_dir}: {", ".join(local) or "none"}')
        export = NodeExport(path)
        if export.manifest.get('game_version') != spec:
            raise SystemExit(f'{path} is the export of game version {export.manifest.get("game_version")}, not {spec}')
    else:
        raise SystemExit(f'{spec}: neither a game version nor an existing node export')
    schema = export.manifest.get('schema', NODES_SCHEMA)
    if schema != NODES_SCHEMA:
        raise SystemExit(f'{export.location} uses record schema {schema}, this script reads schema {NODES_SCHEMA}')
    return export


def neighbours(path):
    """Sectors of the same grid and level around <path> (26 cells)."""
    folder, name = path.rsplit('\\', 1)
    m = SECTOR_RX.match(name)
    if not m:
        return []
    x, y, z = int(m['x']), int(m['y']), int(m['z'])
    return [f"{folder}\\{m['prefix']}{x + dx}_{y + dy}_{z + dz}_{m['lod']}.streamingsector"
            for dx in (-1, 0, 1) for dy in (-1, 0, 1) for dz in (-1, 0, 1) if (dx, dy, dz) != (0, 0, 0)]


def other_levels(path, positions):
    """Sectors of every grid level (same folder and prefix) around these world positions, <path> and its neighbours
    excluded: 2.5 moved some nodes and actors to a sector of another level."""
    folder, name = path.rsplit('\\', 1)
    m = SECTOR_RX.match(name)
    size = m and CELL_SIZES.get(m['prefix'])
    if not size:
        return []
    skip = {path, *neighbours(path)}
    found = {}
    for level in GRID_LEVELS:
        edge = size << level
        for p in positions:
            x, y, z = (math.floor(c / edge) for c in p[:3])
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for dz in (-1, 0, 1):
                        s = f"{folder}\\{m['prefix']}{x + dx}_{y + dy}_{z + dz}_{level}.streamingsector"
                        if s not in skip:
                            found[s] = None
    return list(found)


# ---------------------------------------------------------------- matching

def distance(a, b):
    return math.dist(a[:3], b[:3])


def same_orientation(a, b):
    return abs(sum(x * y for x, y in zip(a, b))) >= 0.9999


def same_element(a, b):
    """Same actor / instance: same world position, orientation and shape count / scale. Actors often share a
    position and differ only by their orientation or shape count."""
    return distance(a, b) <= ELEMENT_DISTANCE and (len(a) < 7 or same_orientation(a[3:7], b[3:7])) \
        and all(abs(x - y) <= 1e-3 for x, y in zip(a[7:], b[7:]))


def element_match(old, new):
    """Fraction of old elements (actors / instances) found in new (see same_element)."""
    if not old:
        return 1.0 if not new else 0.0
    if not new:
        return 0.0
    grid = defaultdict(list)
    for e in new:
        grid[tuple(round(c / ELEMENT_DISTANCE) for c in e[:3])].append(e)
    found = 0
    for e in old:
        key = tuple(round(c / ELEMENT_DISTANCE) for c in e[:3])
        cells = [(key[0] + i, key[1] + j, key[2] + k) for i in (-1, 0, 1) for j in (-1, 0, 1) for k in (-1, 0, 1)]
        if any(same_element(e, n) for c in cells for n in grid.get(c, ())):
            found += 1
    return found / len(old)


def evaluate(old, new, radius):
    """Return (level, sort key, details) for new as a replacement of old, or None if it cannot be the same node."""
    if old.family != new.family:
        return None
    if old.resources and new.resources and not (old.resources & new.resources):
        return None
    res_score = len(old.resources & new.resources) / len(old.resources | new.resources) if old.resources | new.resources else 1.0
    dist = distance(old.position, new.position)
    elements = old.elements is not None and new.elements is not None
    elem_score = element_match(old.elements, new.elements) if elements else None
    names_agree = old.debug_name is not None and old.debug_name == new.debug_name
    identified = res_score > 0 or names_agree  # something besides the position says it is the same node

    if elements and old.elements:
        # Instanced nodes often sit at the origin: their instances / actors locate them, unless they are stored
        # relative to the node (an identical node elsewhere would match).
        if old.type in RELATIVE_ELEMENTS and dist > radius:
            return None
        if elem_score >= 0.999 and len(old.elements) == len(new.elements) and res_score == 1.0:
            level = 'exact'
        elif elem_score >= 0.5:
            level = 'match'
        else:
            return None
    elif dist <= EXACT_DISTANCE and same_orientation(old.orientation, new.orientation) and res_score == 1.0 \
            and (identified or not old.resources):
        level = 'exact'
    elif dist <= NEAR_DISTANCE and (identified or not old.resources):
        level = 'match'
    elif dist <= radius and identified and res_score == 1.0:
        level = 'moved'
    else:
        return None
    key = (LEVELS.index(level), -(elem_score if elem_score is not None else 1.0), -res_score, -int(names_agree), dist)
    return level, key, {'distance': dist, 'resources': res_score, 'elements': elem_score}


def candidates(old, sectors, radius):
    """All acceptable (level, key, sector path, record, details) for old, best first."""
    found = []
    for path, records in sectors:
        for new in records:
            r = evaluate(old, new, radius)
            if r:
                level, key, details = r
                found.append((level, key + (abs(new.index - old.index),), path, new, details))
    found.sort(key=lambda c: c[1])
    return found


def source_mismatch(node, old):
    """Why the file's node does not describe the source node at its index, or None when it does."""
    file_type = node.get('type')
    if file_type and file_type.replace('worldCooked', 'world', 1) != old.family:
        return f'file says {file_type}, source node #{old.index} is {old.type}'
    name = node.get('debugName')
    if name not in (None, '', 'None') and old.debug_name is not None and name != old.debug_name:
        return f"file says debugName '{name}', source node #{old.index} is '{old.debug_name}'"
    for list_key, count_key in ELEMENT_KEYS:
        if count_key in node and old.elements is not None and node[count_key] != len(old.elements):
            return f'file expects {node[count_key]} {list_key[:-9]}s, source node #{old.index} has {len(old.elements)}'
    return None


def element_index(entry):
    """Actor / instance index of an element list entry: 3, [actor, shape] or {index: 3, position: ...}."""
    return entry.get('index', -1) if isinstance(entry, dict) else entry[0] if isinstance(entry, list) else entry


def with_element_index(entry, index):
    if isinstance(entry, dict):
        return {**entry, 'index': index}
    return [index, *entry[1:]] if isinstance(entry, list) else index


def similarity(old, new):
    """Report details of new as a replacement of old, for a node that evaluate() did not propose."""
    union = old.resources | new.resources
    elements = old.elements is not None and new.elements is not None
    return {'distance': distance(old.position, new.position),
            'resources': len(old.resources & new.resources) / len(union) if union else 1.0,
            'elements': element_match(old.elements, new.elements) if elements else None}


def can_hold(old, new):
    """new can hold actors / instances of old: same family and, when both have resources, a shared one."""
    return old.family == new.family and new.elements is not None \
        and not (old.resources and new.resources and not old.resources & new.resources)


def locate_elements(source, old, ks, pool, mapping, located, owners):
    """Find the elements ks of old (actors / instances, by world transform: see same_element) in the nodes of pool
    [(sector path, record)], tried in order; among identical elements, the closest index is kept. mapping receives
    old element -> (sector path, record, new element). located and owners are shared by the whole file:
    (source sector, source node, old element) -> where it was found, and the reverse, so that an element listed
    several times is found once and two source elements never take the same new one."""
    for k in ks:
        if k in mapping or not 0 <= k < len(old.elements):
            continue
        key = (*source, k)
        if key in located:
            mapping[k] = located[key]
            continue
        e = old.elements[k]
        for path, new in pool:
            best = min(((distance(e, x), abs(j - k), j) for j, x in enumerate(new.elements or [])
                        if (path, new.index, j) not in owners and same_element(e, x)), default=None)
            if best:
                mapping[k] = located[key] = path, new, best[2]
                owners[path, new.index, best[2]] = key
                break


# ---------------------------------------------------------------- xl file

# Element lists read by ArchiveXL (deletions in nodeDeletions, mutations in nodeMutations) and their count key.
ELEMENT_KEYS = [('actorDeletions', 'expectedActors'), ('instanceDeletions', 'expectedInstances'),
                ('actorMutations', 'expectedActors'), ('instanceMutations', 'expectedInstances')]


def load_xl(path):
    text = open(path, encoding='utf-8-sig').read()
    # Hand-edited files can carry trailing tabs, which ArchiveXL tolerates but PyYAML rejects.
    text = '\n'.join(line.rstrip() for line in text.splitlines()) + '\n'
    return yaml.safe_load(text), text.lstrip().startswith('{'), text


class IndentDumper(yaml.SafeDumper):
    """Indents sequences under their key, like the .xl files written by WolvenKit and SRU."""

    def increase_indent(self, flow=False, indentless=False):
        return super().increase_indent(flow, False)


def _represent_list(dumper, data):
    # Vectors (position, orientation, scale) stay on one line: [x, y, z, w].
    vector = bool(data) and all(isinstance(v, (int, float)) for v in data) and any(isinstance(v, float) for v in data)
    return dumper.represent_sequence('tag:yaml.org,2002:seq', data, flow_style=vector)


IndentDumper.add_representer(list, _represent_list)


def to_yaml(data):
    return yaml.dump(data, Dumper=IndentDumper, sort_keys=False, allow_unicode=True, default_flow_style=False)


def update(xl, source, target, radius, report, label=''):
    """Match every node of the file. Returns (outcome, extras).
    outcome: {(sector no, kind, node no): (new sector path, updated node, remaps)} for the nodes found, remaps giving
    for each entry of their actor / instance lists its new index, None when not found, or (sector path, node index)
    when the target version moved it to another node; (None, why, None) for the others.
    extras: [(sector path, kind, node)], the entries added for the actors / instances moved to another node."""
    outcome = {}
    rows = {}     # same keys as outcome: report rows, written in the order of the file
    extras = []
    in_sectors = xl['streaming']['sectors']
    show_progress = sys.stderr.isatty()  # no progress lines in redirected output
    not_found = f'not found in {target.version}'

    def was(old, node):
        """What the node was in the source version, to look for it by hand."""
        def at(p):
            return f'at [{", ".join(map(str, p[:3]))}]'

        if old.debug_name:
            what = f"'{old.debug_name}'"
        else:  # no name: its resource, if any
            what = next((v for v in (old.raw.get('r') or {}).values() if '\\' in v), '')
        # Instanced nodes often sit at the origin: give the positions of the actors / instances the file lists.
        where = [f'{list_key[:-9]} {element_index(e)} {at(old.elements[element_index(e)])}'
                 for list_key, _ in ELEMENT_KEYS for e in node.get(list_key) or []
                 if old.elements and 0 <= element_index(e) < len(old.elements)][:3]
        return ' '.join(filter(None, [f'was in {source.version}: {old.type}', what, ', '.join(where) or at(old.position)]))

    def fail(key, path, node, old, why, status='not found', note=''):
        rows[key] = row(path, key[1], node, old, status, note=note)
        outcome[key] = None, why, None

    around = {}  # sector path -> its neighbour sectors in the target version, loaded when needed

    def nearby(path):
        if path not in around:
            around[path] = [(p, r) for p in neighbours(path) if (r := target.sector(p)) is not None]
        return around[path]

    def farther(path, positions):
        return [(p, r) for p in other_levels(path, positions) if (r := target.sector(p)) is not None]

    # Candidates of every node first (a node the file lists several times is looked up once), then assign them.
    searched = {}  # (sector path, source index) -> candidates
    wanted = []
    for done, sector in enumerate(in_sectors):
        if show_progress:
            print(f'\r{label}: {done} / {len(in_sectors)} sectors ({done * 100 // len(in_sectors)}%)', end='', file=sys.stderr)
        path = sector['path']
        old_records = source.sector(path)
        if old_records is None:
            for kind in ('nodeDeletions', 'nodeMutations'):
                for n, node in enumerate(sector.get(kind) or []):
                    fail((done, kind, n), path, node, None, f'sector missing from the {source.version} export',
                         note='sector missing from the source export')
            continue
        if sector.get('expectedNodes') not in (None, len(old_records)):
            print(f'\rwarning: {path} has {len(old_records)} nodes in the source version, the file expects '
                  f'{sector["expectedNodes"]}: it was not made for this source version', file=sys.stderr)

        for kind in ('nodeDeletions', 'nodeMutations'):
            for n, node in enumerate(sector.get(kind) or []):
                idx = node.get('index')
                if not isinstance(idx, int) or isinstance(idx, bool) or idx < 0:
                    fail((done, kind, n), path, node, None, f'invalid index: {idx!r}', note=f'invalid index: {idx!r}')
                    continue
                if idx >= len(old_records):
                    fail((done, kind, n), path, node, None, f'index out of range in {source.version}',
                         note='index out of range in the source version')
                    continue
                old = old_records[idx]
                mismatch = source_mismatch(node, old)
                if mismatch:
                    # The file was not made for the source version (or the node changed): remapping would hit the wrong node.
                    fail((done, kind, n), path, node, old, f'does not match {source.version}: {mismatch}',
                         'source mismatch', mismatch)
                    continue
                if (path, idx) not in searched:
                    here = target.sector(path)
                    found = candidates(old, [(path, here)] if here else [], radius)
                    if not found or found[0][0] == 'moved':
                        found = sorted(found + candidates(old, nearby(path), radius), key=lambda c: c[1])
                    if not found or found[0][0] == 'moved':
                        # The target version also moved nodes to sectors of another grid level.
                        found = sorted(found + candidates(old, farther(path, [old.position]), radius), key=lambda c: c[1])
                    searched[path, idx] = found
                wanted.append((done, kind, n, node, path, old, searched[path, idx]))

    # Best candidates first. Two source nodes never take the same node, except to delete or change some of its
    # actors / instances: the target version gathered those of several source nodes in one node, or spread them.
    assigned = {}  # (sector path, source index) -> candidate taken by that source node
    holder = {}    # (sector path, node index) -> (sector path, source index) that took it
    located, owners = {}, {}  # actors / instances, see locate_elements
    for done, kind, n, node, path, old, found in sorted(wanted, key=lambda w: w[6][0][1] if w[6] else (99,)):
        key = done, kind, n
        source_key = path, old.index
        lists = [(list_key, count_key) for list_key, count_key in ELEMENT_KEYS if isinstance(node.get(list_key), list)]
        note = ''

        if not lists or old.elements is None:
            choice = assigned.get(source_key) or next(
                (c for c in found if holder.get((c[2], c[3].index), source_key) == source_key), None)
            if choice is None:
                fail(key, path, node, old, f'{not_found}\n{was(old, node)}')
                continue
            level, _, new_path, new, details = choice
            if lists and new.elements is not None:
                # No actor data in the source export, unlike the target one: the indices cannot be remapped.
                fail(key, path, node, old, f'none of its {lists[0][0][:-9]}s found in {target.version}\n{was(old, node)}',
                     note=f'{lists[0][0]}: no actor data in the source export')
                continue
            assigned[source_key] = choice
            holder[new_path, new.index] = source_key
            if len([c for c in found if c[1][:4] == choice[1][:4]]) > 1:
                note = (note + '; ' if note else '') + 'several equivalent candidates, closest index kept'
            if new_path != path:
                note = (note + '; ' if note else '') + 'moved to another sector'
            updated = dict(node)
            updated['type'] = new.type
            updated['index'] = new.index
            for list_key, _ in lists:
                # No actor data for this node type in the exports (foliage): the same node keeps its indices.
                note = (note + '; ' if note else '') + f'{list_key} kept as is: not in the node exports'
                if level == 'exact':
                    level = 'match'
            outcome[key] = new_path, updated, {}
            rows[key] = row(path, kind, node, old, level, new_path, new, details, '', note)
            continue

        # Actors / instances, one by one: in the candidate nodes, then (unless they are stored relative to their node)
        # in any node of the same kind in this sector and its neighbours, then in the sectors of other grid levels.
        ks = [element_index(e) for list_key, _ in lists for e in node[list_key]]
        mapping = {}  # old element -> (sector path, record, new element)
        pool = [(c[2], c[3]) for c in found if c[3].elements is not None]
        locate_elements(source_key, old, ks, pool, mapping, located, owners)
        if old.type not in RELATIVE_ELEMENTS:
            tried = {(p, r.index) for p, r in pool}
            for sectors in (lambda: [(path, target.sector(path))] + nearby(path),
                            lambda: farther(path, [old.elements[k] for k in ks
                                                   if k not in mapping and 0 <= k < len(old.elements)])):
                if all(k in mapping or not 0 <= k < len(old.elements) for k in ks):
                    break
                pool = [(p, r) for p, records in sectors() if records
                        for r in sorted((r for r in records if can_hold(old, r)), key=lambda r: abs(r.index - old.index))
                        if (p, r.index) not in tried]
                tried.update((p, r.index) for p, r in pool)
                locate_elements(source_key, old, ks, pool, mapping, located, owners)

        groups = defaultdict(set)  # (sector path, node index) -> old elements found there
        records = {}
        for k, (p, new, _) in mapping.items():
            groups[p, new.index].add(k)
            records[p, new.index] = new
        if not groups:
            # Without any of its actors / instances, a deletion would remove the whole node.
            fail(key, path, node, old, f'none of its {lists[0][0][:-9]}s found in {target.version}\n{was(old, node)}',
                 note=f'{lists[0][0]} not found: {ks}')
            continue
        # The node that got most of them takes the entry, the others get new entries.
        rank = {(c[2], c[3].index): i for i, c in enumerate(found)}
        primary = max(groups, key=lambda g: (len(groups[g]), -rank.get(g, len(found)), g[0] == path,
                                             -abs(g[1] - old.index)))
        new_path, new = primary[0], records[primary]
        choice = next((c for c in found if (c[2], c[3].index) == primary), None)
        level, details = (choice[0], choice[4]) if choice else ('match', similarity(old, new))
        if len([c for c in found if choice and c[1][:4] == choice[1][:4]]) > 1:
            note = (note + '; ' if note else '') + 'several equivalent candidates, closest index kept'
        if new_path != path:
            note = (note + '; ' if note else '') + 'moved to another sector'
        updated = dict(node)
        updated['type'] = new.type
        updated['index'] = new.index
        remaps = {}
        spread = defaultdict(dict)  # (sector path, node index) -> list key -> entries moved there
        actors = []
        lost = None
        for list_key, count_key in lists:
            entries = node[list_key]
            mapped = []
            for e in entries:
                m = mapping.get(element_index(e))
                if m is None:
                    mapped.append(None)
                elif (m[0], m[1].index) == primary:
                    mapped.append(m[2])
                else:
                    mapped.append((m[0], m[1].index))
                    spread[m[0], m[1].index].setdefault(list_key, []).append(with_element_index(e, m[2]))
            remaps[list_key] = mapped
            updated[list_key] = [with_element_index(e, j) for e, j in zip(entries, mapped) if isinstance(j, int)]
            updated[count_key] = len(new.elements)
            actors.append(f'{sum(j is not None for j in mapped)}/{len(entries)}')
            missing = [element_index(e) for e, j in zip(entries, mapped) if j is None]
            if missing:
                note = (note + '; ' if note else '') + f'{list_key} not found: {missing}'
                if len(missing) == len(entries):
                    lost = list_key
                if level == 'exact':
                    level = 'match'
        if lost:
            fail(key, path, node, old, f'none of its {lost[:-9]}s found in {target.version}\n{was(old, node)}', note=note)
            continue
        for (p, i), moved in spread.items():
            other = records[p, i]
            entry = {'index': other.index, 'type': other.type}
            where = '' if p == new_path else ' of ' + p.rsplit('\\', 1)[-1]
            for list_key, count_key in lists:
                if list_key in moved:
                    entry[count_key] = len(other.elements)
                    entry[list_key] = moved[list_key]
                    note = (note + '; ' if note else '') + \
                        f'{list_key} {[element_index(e) for e in moved[list_key]]} moved to node {other.index}{where}'
            extras.append((p, kind, entry))
        if spread and level == 'exact':
            level = 'match'
        outcome[key] = new_path, updated, remaps
        rows[key] = row(path, kind, node, old, level, new_path, new, details, ' '.join(actors), note)

    kinds = ('nodeDeletions', 'nodeMutations')
    report.extend(rows[k] for k in sorted(rows, key=lambda k: (k[0], kinds.index(k[1]), k[2])))
    if show_progress:
        print(f'\r{label}: {len(in_sectors)} / {len(in_sectors)} sectors (100%)', file=sys.stderr)
    return outcome, extras


def rebuild(xl, outcome, extras, target):
    """The converted file as data: one entry per sector, sorted by path, nodes sorted by index."""
    out_sectors = {}
    nodes = [(new_path, kind, updated) for (_, kind, _), (new_path, updated, _) in outcome.items()] + extras
    for new_path, kind, updated in nodes:
        if new_path is None:
            continue
        if new_path not in out_sectors:
            out_sectors[new_path] = {'nodeDeletions': [], 'nodeMutations': [],
                                     'expectedNodes': len(target.sector(new_path)), 'path': new_path}
        out_sectors[new_path][kind].append(updated)

    sectors = []
    for s in out_sectors.values():
        s['nodeDeletions'].sort(key=lambda n: n['index'])
        s['nodeMutations'].sort(key=lambda n: n['index'])
        for kind in ('nodeDeletions', 'nodeMutations'):
            if not s[kind]:
                del s[kind]
        sectors.append(s)
    result = dict(xl)
    result['streaming'] = dict(xl['streaming'])
    result['streaming']['sectors'] = sorted(sectors, key=lambda s: s['path'])
    return result


def flow(value):
    return '[' + ', '.join(map(str, value)) + ']' if isinstance(value, list) else str(value)


def patch_yaml(text, xl, outcome, extras, target):
    """The converted file as YAML, written into the input text so that its comments, order and layout stay.
    A node without a match is commented out where it was, with the reason; a node found in another sector is
    commented out too and added to that sector, like the extras (actors / instances moved to another node)."""
    root = yaml.compose(text)
    edits = []  # (start, end, replacement) in the text
    kinds = ('nodeDeletions', 'nodeMutations')

    def fields(mapping):
        return {k.value: (k, v) for k, v in mapping.value}

    def end_of(node):
        # A block collection ends at the next token, past the comments after it: take its last leaf instead.
        while isinstance(node, yaml.CollectionNode) and not node.flow_style and node.value:
            node = node.value[-1][1] if isinstance(node, yaml.MappingNode) else node.value[-1]
        return node.end_mark.index

    def line_start(i):
        return text.rfind('\n', 0, i) + 1

    def line_end(i):
        return text.find('\n', i)

    def dash(item):
        return text.rfind('-', 0, item.start_mark.index)  # the "- " that opens a sequence item

    def comment_out(start, end, note=None):
        a, b = line_start(start), line_end(end - 1)
        col = start - a
        lines = []
        for line in text[a:b].split('\n'):
            indent = min(col, len(line) - len(line.lstrip()))
            lines.append(line if not line.strip() or line.lstrip().startswith('#') else f'{line[:indent]}# {line[indent:]}')
        if note:
            lines[:0] = [f'{" " * col}# axl_sector_updater: {line}' for line in note.split('\n')]
        edits.append((a, b, '\n'.join(lines)))

    def set_value(mapping, key, value, after):
        """Give key this value, on a new line after the <after> key when it is missing."""
        found = fields(mapping)
        if key in found:
            k, v = found[key]
            edits.append((k.end_mark.index, end_of(v), f': {flow(value)}'))
        else:
            k, v = found.get(after) or mapping.value[0]
            at = line_end(end_of(v) - 1)
            edits.append((at, at, f'\n{" " * k.start_mark.column}{key}: {flow(value)}'))

    def renumber(values, mapped):
        """Renumber a block list of actors / instances in place; the entries not found or moved to another node are
        commented out."""
        for entry, j in zip(values.value, mapped):
            if j is None:
                comment_out(dash(entry), end_of(entry), f'not found in {target.version}')
                continue
            if isinstance(j, tuple):
                name = j[0].rsplit('\\', 1)[-1]
                comment_out(dash(entry), end_of(entry), f'moved to node {j[1]} of {name} in {target.version}')
                continue
            if isinstance(entry, yaml.MappingNode):
                index = fields(entry)['index'][1]
            else:
                index = entry.value[0] if isinstance(entry, yaml.SequenceNode) else entry
            if index.value != str(j):
                edits.append((index.start_mark.index, index.end_mark.index, str(j)))

    def block(data, col):
        return ''.join(f'\n{" " * col}{line}' for line in to_yaml(data).splitlines())

    in_sectors = xl['streaming']['sectors']
    entries = fields(fields(root)['streaming'][1])['sectors'][1]
    kept = {si for (si, _, _), (new_path, _, _) in outcome.items() if new_path == in_sectors[si]['path']}
    home = {}  # sector path -> first entry keeping nodes there, which receives the nodes that moved to that sector
    for si in sorted(kept):
        home.setdefault(in_sectors[si]['path'], si)
    arriving = defaultdict(lambda: defaultdict(list))  # sector path -> kind -> nodes that moved there
    dead_lists = set()

    for si, (sector, entry) in enumerate(zip(in_sectors, entries.value)):
        path = sector['path']
        if si in kept:
            expected = len(target.sector(path))
            if sector.get('expectedNodes') != expected:
                set_value(entry, 'expectedNodes', expected, 'path')
        for i, (k, v) in enumerate(entry.value):
            start = dash(entry) if i == 0 else k.start_mark.index
            if k.value not in kinds or not isinstance(v, yaml.SequenceNode):
                if si not in kept:  # nothing left in this entry
                    comment_out(start, end_of(v))
                continue
            nodes = sector.get(k.value) or []
            gone = 0
            for n, (node, item) in enumerate(zip(nodes, v.value)):
                new_path, updated, remaps = outcome[si, k.value, n]
                if new_path == path:
                    item_fields = fields(item)
                    for key, value in updated.items():
                        values = item_fields.get(key, (None, None))[1]
                        if key in remaps and isinstance(values, yaml.SequenceNode) and not values.flow_style:
                            renumber(values, remaps[key])
                        elif key not in node or node[key] != value:
                            after = next((l for l, c in ELEMENT_KEYS if c == key and l in node), 'index')
                            set_value(item, key, value, after)
                    continue
                gone += 1
                if new_path is None:
                    note = updated
                else:
                    name = new_path.rsplit('\\', 1)[-1]
                    note = f'moved to {name} in {target.version}'
                    arriving[new_path][k.value].append(updated)
                comment_out(dash(item), end_of(item), note)
            if si not in kept or (nodes and gone == len(nodes)):
                comment_out(start, k.end_mark.index)  # the key line; its nodes are commented out above
                dead_lists.add((si, k.value))

    for path, kind, node in extras:
        arriving[path][kind].append(node)
    for path, moved in arriving.items():
        for nodes in moved.values():
            nodes.sort(key=lambda n: n['index'])
        if path not in home:  # new sector entry at the end of the list
            col = dash(entries.value[0]) - line_start(entries.value[0].start_mark.index)
            data = [{'path': path, 'expectedNodes': len(target.sector(path)), **moved}]
            at = line_end(end_of(entries) - 1)
            edits.append((at, at, block(data, col)))
            continue
        entry = entries.value[home[path]]
        for kind, nodes in moved.items():
            existing = fields(entry).get(kind)
            if existing and isinstance(existing[1], yaml.SequenceNode) and existing[1].value \
                    and (home[path], kind) not in dead_lists:
                first = existing[1].value[0]
                at = line_end(end_of(existing[1]) - 1)
                edits.append((at, at, block(nodes, dash(first) - line_start(first.start_mark.index))))
            else:
                at = line_end(end_of(entry) - 1)
                edits.append((at, at, block({kind: nodes}, entry.value[0][0].start_mark.column)))

    out, pos = [], 0
    for start, end, new in sorted(edits, key=lambda e: e[0]):  # stable: insertions at one place keep their order
        assert start >= pos, 'overlapping edits'
        out += [text[pos:start], new]
        pos = end
    out.append(text[pos:])
    return ''.join(out)


def row(path, kind, node, old, status, new_path=None, new=None, details=None, actors='', note=''):
    return {
        'status': status,
        'sector': path.rsplit('\\', 1)[-1],
        'kind': kind,
        'old_index': node.get('index'),
        'old_type': node.get('type'),
        'debugName': node.get('debugName') or (old.debug_name if old else ''),
        'new_sector': '' if new_path in (None, path) else new_path.rsplit('\\', 1)[-1],
        'new_index': '' if new is None else new.index,
        'new_type': '' if new is None else new.type,
        'distance_m': '' if details is None else f"{details['distance']:.4f}",
        'resources': '' if details is None else f"{details['resources']:.2f}",
        'elements': '' if details is None or details['elements'] is None else f"{details['elements']:.2f}",
        'actors': actors,
        'note': note,
    }


def is_xl(path):
    return path.lower().endswith(XL_EXTENSIONS)


def jobs(input_path, output_path, yaml_out):
    """(input file, output file) pairs. A folder input is walked recursively and mirrored into the output folder.
    With yaml_out, an output named after a .json input becomes .xl."""

    def output_name(name):
        root, ext = os.path.splitext(name)
        return root + '.xl' if yaml_out and ext.lower() == '.json' else name

    if os.path.isdir(input_path):
        if is_xl(output_path) and not os.path.isdir(output_path):
            raise SystemExit(f'{input_path} is a folder: the output must be a folder too')
        found = []
        for folder, _, files in os.walk(input_path):
            for name in files:
                if is_xl(name):
                    path = os.path.join(folder, name)
                    found.append((path, os.path.join(output_path, output_name(os.path.relpath(path, input_path)))))
        outputs = defaultdict(list)
        for path, out in found:
            outputs[os.path.normcase(out)].append(path)
        for paths in outputs.values():
            if len(paths) > 1:
                raise SystemExit(f'{" and ".join(sorted(paths))} would both be written to the same output file')
        return sorted(found)
    if not os.path.isfile(input_path):
        raise SystemExit(f'{input_path} does not exist')
    if os.path.isdir(output_path) or not is_xl(output_path):
        output_path = os.path.join(output_path, output_name(os.path.basename(input_path)))
    return [(input_path, output_path)]


def convert(input_path, output_path, source, target, radius, debug, output_format='yaml'):
    """Convert one file. Returns the report rows, or None when the file has no streaming sectors.
    output_format 'yaml' edits the input text, which keeps its comments and order (a JSON input is first written as
    YAML); 'json' rewrites the file."""
    xl, in_json, text = load_xl(input_path)
    if not isinstance(xl, dict) or not isinstance(xl.get('streaming'), dict) or not xl['streaming'].get('sectors'):
        return None
    if os.path.abspath(input_path) == os.path.abspath(output_path):
        raise SystemExit(f'{input_path}: the output would overwrite the input')
    report = []
    outcome, extras = update(xl, source, target, radius, report, os.path.basename(input_path))
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, 'w', encoding='utf-8', newline='\n') as f:
        if output_format == 'json':
            json.dump(rebuild(xl, outcome, extras, target), f, indent=2, ensure_ascii=False)
        else:
            f.write(patch_yaml(to_yaml(xl) if in_json else text, xl, outcome, extras, target))
    if debug:
        with open(output_path + '.report.tsv', 'w', encoding='utf-8', newline='') as f:
            w = csv.DictWriter(f, fieldnames=list(report[0].keys()) if report else ['status'], delimiter='\t')
            w.writeheader()
            w.writerows(report)
    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('input', nargs='?', default=INPUT_DIR, help='.xl file or folder (default: input/)')
    ap.add_argument('output', nargs='?', default=OUTPUT_DIR, help='.xl file or folder (default: output/)')
    ap.add_argument('--from', dest='source', required=True,
                    help='game version the files were made for (e.g. 2.31), or a node export zip / folder')
    ap.add_argument('--to', dest='target', required=True, help='target game version (e.g. 2.5), or a node export')
    ap.add_argument('--versions-dir', default=VERSIONS_DIR, help='where node exports are kept and downloaded (default: versions/)')
    ap.add_argument('--release-url', default=RELEASE_URL, help='where missing node exports are downloaded from')
    ap.add_argument('--radius', type=float, default=2.0,
                    help='max distance (m) to accept a node that moved, when its resources still match (default 2)')
    ap.add_argument('--format', type=str.lower, choices=['yaml', 'yml', 'json'], default='yaml',
                    help='write the output as YAML (yaml or yml: comments kept, nodes not found commented out) '
                         'or JSON (default: yaml)')
    ap.add_argument('--debug', action='store_true',
                    help='write a report next to each output file (<output>.report.tsv): how every node was matched')
    args = ap.parse_args()
    output_format = {'yml': 'yaml'}.get(args.format, args.format)

    todo = jobs(args.input, args.output, output_format == 'yaml')
    if not todo:
        raise SystemExit(f'no .xl file in {args.input}')
    source = open_export(args.source, args.versions_dir, args.release_url)
    target = open_export(args.target, args.versions_dir, args.release_url)
    if source.version == target.version:
        raise SystemExit(f'source and target are both game version {source.version}')
    print(f'{source.version} -> {target.version}, {len(todo)} file(s)')

    totals = defaultdict(int)
    for input_path, output_path in todo:
        report = convert(input_path, output_path, source, target, args.radius, args.debug, output_format)
        if report is None:
            print(f'\n{input_path}: no streaming sectors, skipped')
            continue
        counts = defaultdict(int)
        for r in report:
            counts[r['status']] += 1
            totals[r['status']] += 1
        print(f'\n{input_path}\n  {len(report)} nodes: ' + ', '.join(f'{counts[s]} {s}' for s in STATUSES if counts[s]))
        for r in report:
            if r['status'] != 'exact' or r['note']:
                print(f"    {r['status']:9} {r['sector']} #{r['old_index']} {r['old_type']} '{r['debugName']}' -> "
                      f"{r['new_sector'] or 'same sector'} #{r['new_index']} {r['note']}")
        print(f'  -> {output_path}')
    if len(todo) > 1:
        print(f'\ntotal {sum(totals.values())} nodes: ' + ', '.join(f'{totals[s]} {s}' for s in STATUSES if totals[s]))


if __name__ == '__main__':
    main()
