# Copyright © 2026 Aurélien "Akiway" Klein.
"""Update ArchiveXL sector node removals from one game version to another.

Usage:
    python axl_sector_updater.py --from 2.31 --to 2.5 [input] [output] [options]

input / output are an .xl file or a folder (default: the input/ and output/ folders next to this script).
--from / --to are game versions. Their node exports (nodes-<version>.zip: one <sector path>.jsonl per sector, one
record per nodeData entry, see docs/NODE_RECORDS.md) are read from the versions/ folder and downloaded from the GitHub
release when missing; a path to an export zip or unpacked folder also works.

Every removal is looked up by what the node *is* (node family, resources, world position, debugName,
actor/instance positions) instead of by index, then written back with its new index, type, expectedNodes and
remapped actorDeletions. With --debug, a report (<output>.report.tsv) lists how each removal was matched so that
anything uncertain can be checked by hand.
"""
import argparse, csv, functools, json, math, os, re, sys, urllib.error, urllib.request, zipfile
from collections import defaultdict

import yaml

EXACT_DISTANCE = 0.01   # m: same placement
NEAR_DISTANCE = 0.05    # m: same placement after re-cooking (float noise, re-snapped transforms)
ELEMENT_DISTANCE = 0.01 # m: same actor / instance
SECTOR_RX = re.compile(r'^(?P<prefix>.*_)(?P<x>-?\d+)_(?P<y>-?\d+)_(?P<z>-?\d+)_(?P<lod>\d+)\.streamingsector$')
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


# ---------------------------------------------------------------- matching

def distance(a, b):
    return math.dist(a[:3], b[:3])


def same_orientation(a, b):
    return abs(sum(x * y for x, y in zip(a, b))) >= 0.9999


def element_match(old, new):
    """Fraction of old elements (actors / instances) found at the same world position in new."""
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
        if any(distance(e, n) <= ELEMENT_DISTANCE for c in cells for n in grid.get(c, ())):
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
        # Instanced nodes often sit at the origin: their instances / actors locate them.
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


def remap_elements(old, new, indices):
    """Map old actor / instance indices to the new node's indices by world position."""
    mapped, missing = [], []
    used = set()
    for i in indices:
        if old.elements is None or i >= len(old.elements):
            missing.append(i)
            continue
        best = min(((distance(old.elements[i], e), j) for j, e in enumerate(new.elements or []) if j not in used),
                   default=None)
        if best and best[0] <= ELEMENT_DISTANCE:
            mapped.append(best[1])
            used.add(best[1])
        else:
            missing.append(i)
    return sorted(mapped), missing


# ---------------------------------------------------------------- xl file

ELEMENT_KEYS = [('actorDeletions', 'expectedActors'), ('elementDeletions', 'expectedElements')]


def load_xl(path):
    text = open(path, encoding='utf-8-sig').read()
    # Hand-edited files can carry trailing tabs, which ArchiveXL tolerates but PyYAML rejects.
    text = '\n'.join(line.rstrip() for line in text.splitlines())
    return yaml.safe_load(text), text.lstrip().startswith('{')


class IndentDumper(yaml.SafeDumper):
    """Indents sequences under their key, like the .xl files written by WolvenKit and SRU."""

    def increase_indent(self, flow=False, indentless=False):
        return super().increase_indent(flow, False)


def _represent_list(dumper, data):
    # Vectors (position, orientation, scale) stay on one line: [x, y, z, w].
    vector = bool(data) and all(isinstance(v, (int, float)) for v in data) and any(isinstance(v, float) for v in data)
    return dumper.represent_sequence('tag:yaml.org,2002:seq', data, flow_style=vector)


IndentDumper.add_representer(list, _represent_list)


def save_xl(path, data, as_json):
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        if as_json:
            json.dump(data, f, indent=2, ensure_ascii=False)
        else:
            yaml.dump(data, f, Dumper=IndentDumper, sort_keys=False, allow_unicode=True, default_flow_style=False)


def update(xl, source, target, radius, report):
    out_sectors = {}

    def out_sector(path):
        if path not in out_sectors:
            out_sectors[path] = {'nodeDeletions': [], 'nodeMutations': [],
                                 'expectedNodes': len(target.sector(path)), 'path': path}
        return out_sectors[path]

    for sector in xl['streaming']['sectors']:
        path = sector['path']
        old_records = source.sector(path)
        if old_records is None:
            for kind in ('nodeDeletions', 'nodeMutations'):
                for node in sector.get(kind) or []:
                    report.append(row(path, kind, node, None, 'not found', note='sector missing from the source export'))
            continue
        if sector.get('expectedNodes') not in (None, len(old_records)):
            print(f'warning: {path} has {len(old_records)} nodes in the source version, the file expects '
                  f'{sector["expectedNodes"]}: it was not made for this source version', file=sys.stderr)

        here = target.sector(path)
        around = None  # neighbour sectors, loaded only when a node is not found in its own sector

        # Best candidate of every node first, then assign greedily so two removals never take the same node.
        wanted = []
        for kind in ('nodeDeletions', 'nodeMutations'):
            for node in sector.get(kind) or []:
                idx = node['index']
                if idx >= len(old_records):
                    report.append(row(path, kind, node, None, 'not found', note='index out of range in the source version'))
                    continue
                old = old_records[idx]
                mismatch = source_mismatch(node, old)
                if mismatch:
                    # The file was not made for the source version (or the node changed): remapping would hit the wrong node.
                    report.append(row(path, kind, node, old, 'source mismatch', note=mismatch))
                    continue
                note = ''
                found = candidates(old, [(path, here)] if here else [], radius)
                if not found or found[0][0] == 'moved':
                    if around is None:
                        around = [(p, r) for p in neighbours(path) if (r := target.sector(p)) is not None]
                    found = sorted(found + candidates(old, around, radius), key=lambda c: c[1])
                wanted.append((kind, node, old, found, note))

        taken = set()
        for kind, node, old, found, note in sorted(wanted, key=lambda w: w[3][0][1] if w[3] else (99,)):
            choice = next((c for c in found if (c[2], c[3].index) not in taken), None)
            if choice is None:
                report.append(row(path, kind, node, old, 'not found', note=note))
                continue
            level, _, new_path, new, details = choice
            taken.add((new_path, new.index))
            if len([c for c in found if c[1][:4] == choice[1][:4]]) > 1:
                note = (note + '; ' if note else '') + 'several equivalent candidates, closest index kept'
            if new_path != path:
                note = (note + '; ' if note else '') + 'moved to another sector'

            updated = dict(node)
            updated['type'] = new.type
            updated['index'] = new.index
            actors = ''
            for list_key, count_key in ELEMENT_KEYS:
                if list_key in node:
                    mapped, missing = remap_elements(old, new, node[list_key])
                    updated[list_key] = mapped
                    updated[count_key] = len(new.elements or [])
                    actors = f'{len(mapped)}/{len(node[list_key])}'
                    if missing:
                        note = (note + '; ' if note else '') + f'{list_key} not found: {missing}'
                        if level == 'exact':
                            level = 'match'
            out_sector(new_path)[kind].append(updated)
            report.append(row(path, kind, node, old, level, new_path, new, details, actors, note))

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


def jobs(input_path, output_path):
    """(input file, output file) pairs. A folder input is walked recursively and mirrored into the output folder."""
    if os.path.isdir(input_path):
        if is_xl(output_path) and not os.path.isdir(output_path):
            raise SystemExit(f'{input_path} is a folder: the output must be a folder too')
        found = []
        for folder, _, files in os.walk(input_path):
            for name in files:
                if is_xl(name):
                    path = os.path.join(folder, name)
                    found.append((path, os.path.join(output_path, os.path.relpath(path, input_path))))
        return sorted(found)
    if not os.path.isfile(input_path):
        raise SystemExit(f'{input_path} does not exist')
    if os.path.isdir(output_path) or not is_xl(output_path):
        output_path = os.path.join(output_path, os.path.basename(input_path))
    return [(input_path, output_path)]


def convert(input_path, output_path, source, target, radius, debug, output_format=None):
    """Convert one file. Returns the report rows, or None when the file has no streaming sectors.
    output_format: 'json' or 'yaml'; None keeps the format of the input."""
    xl, as_json = load_xl(input_path)
    if output_format:
        as_json = output_format == 'json'
    if not isinstance(xl, dict) or not isinstance(xl.get('streaming'), dict) or not xl['streaming'].get('sectors'):
        return None
    if os.path.abspath(input_path) == os.path.abspath(output_path):
        raise SystemExit(f'{input_path}: the output would overwrite the input')
    report = []
    result = update(xl, source, target, radius, report)
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    save_xl(output_path, result, as_json)
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
    ap.add_argument('--format', type=str.lower, choices=['yaml', 'yml', 'json'],
                    help='write the output content as YAML (yaml or yml) or JSON (default: same as the input)')
    ap.add_argument('--debug', action='store_true',
                    help='write a report next to each output file (<output>.report.tsv): how every node was matched')
    args = ap.parse_args()
    output_format = {'yml': 'yaml'}.get(args.format, args.format)

    todo = jobs(args.input, args.output)
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
