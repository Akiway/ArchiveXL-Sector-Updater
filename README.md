# ArchiveXL Sector Updater

Converts the sector edits of an ArchiveXL `.xl` file (node removals `nodeDeletions` and mutations `nodeMutations`)
from one Cyberpunk 2077 version to another, for example from 2.31 to 2.5.

**Why?** Patch 2.5 merged many world objects (meshes, decals, collisions, proxies…) into new "cooked" nodes and
reordered every sector: the node indices of a 2.31 `.xl` now point to the wrong objects. This tool finds each node
again by what it **is** (type, resources, position, name, actors / instances), then writes its new index and type,
`expectedNodes` and its actor / instance lists (`actorDeletions`, `instanceDeletions`, `actorMutations`,
`instanceMutations` and their `expectedActors` / `expectedInstances`).

## Installation (Windows)

1. Install **Python 3.14** from [python.org](https://www.python.org/downloads/windows/). If the installer shows the
   option, tick **"Add python.exe to PATH"**. Python 3.10 to 3.14 work; **3.15 does not work yet**
   (see [FAQ](#faq)).
2. Unzip the tool anywhere.
3. Open a terminal in the tool folder: in File Explorer, open the folder, right-click an empty area and choose
   **Open in Terminal** (Windows 10: Shift + right-click, **Open PowerShell window here**).
4. Install the library the tool needs (only once):

   ```
   py -3.14 -m pip install -r requirements.txt
   ```

## Usage

1. Put your `.xl` files in the `input` folder (subfolders are fine).
2. In the terminal, run:

   ```
   py -3.14 axl_sector_updater.py --from 2.31 --to 2.5
   ```

   `--from` is the game version your files were made for, `--to` the version to convert them to. A file that starts
   with a `# Game version: <version>` line (as the converted files do) is converted from that version instead, so a
   file converted before can go on to a newer version without `--from` (see [Options](#options)). When there is no
   node export for that version (or it cannot be downloaded), the file is skipped and listed at the end, the others
   are converted; `--force-from` converts it from `--from` instead.
3. The converted files are in the `output` folder, with the same folder layout as `input`. They are YAML files with
   your comments kept (see [The output file](#the-output-file)).

The first run downloads the game data of both versions (`nodes-2.31.zip` and `nodes-2.5.zip`, ≈ 350 MB each) from
the [`node-exports`](https://github.com/Akiway/ArchiveXL-Sector-Updater/releases/tag/node-exports) release into the
`versions` folder. Later runs reuse them.

Available versions: `1.63`, `2.02`, `2.13`, `2.2`, `2.21`, `2.31`, `2.5`.

### Reading the result

While a file is converted, the console shows its progress (`INCF.xl: 523 / 1953 sectors (26%)`). Then it prints
the file's warnings, how many nodes got each status, and every node that is not `exact` (nor `other mod`):

```
input\ef_bugbears_apt.xl (2.31 -> 2.5)
  warning: expectedNodes differs from 2.31 in 4 / 4 sector(s): it matches no exported version
  35 nodes: 6 exact, 10 by name, 19 source mismatch
    ...
```

The warnings are:

- `expectedNodes differs from <version>`: the node counts the file gives (`expectedNodes`) are not those of the
  `--from` version, so the file was probably made for another one. The tool compares them with every node export
  in the `versions` folder and names the version they match (`the file was made for 2.13`), or the closest ones.
  When only some sectors differ, they are listed: they may have been changed by another mod or by hand. See
  [the FAQ](#results).
- `not valid UTF-8`: the file has corrupted debugNames, replaced in the output (see
  [The output file](#the-output-file)). Nothing to do.

With several files, a table at the end gives one line per file, those to look at first on top: files not
converted, then nodes lost (`not found`, `source mismatch`), warnings, and nodes to check in game.

```
file                          from  nodes  exact  match  by name  not found  source mismatch  warnings
----------------------------  ----  -----  -----  -----  -------  ---------  ---------------  -------------
file_1.xl                     2.31     35      6      -       10          -               19  expectedNodes
file_2.xl                     2.31    129    116      -        6          3                4  not UTF-8
file_3.xl                     2.31      7      7      -        -          -                -
...
```

Last, a `What to do` section groups the nodes left out (`source mismatch`, `not found`) by cause, with the files
concerned and what to try: convert the file from another version, fix an index by hand, look for an object in
game…

Everything the console prints (except the progress) is also written to the `logs` folder, one file per run
(`2026-11-10_01-58-51.log`); the last 10 runs are kept.

| Status | Meaning | In the output |
|---|---|---|
| `exact` | found, identical (same type, resources, position within 1 cm, every actor / instance) | yes |
| `match` | found, nearly identical (within 5 cm, or only part of its actors / instances found) | yes, **check it in game** |
| `moved` | same object, moved by more than 5 cm (up to `--radius`, 2 m by default) | yes, **check it in game** |
| `not found` | no such object in the new version, neither in the sector, nor in its 26 neighbours, nor in the sectors of the other grid levels around it, or none of the actors / instances it lists | commented out |
| `by name` | the node at its index does not match the `--from` version, but its debugName names one other node of the sector: that node was converted (see below) | yes, **check it in game** |
| `already target` | the node does not match the `--from` version but matches the `--to` one at the same index: the file mixes both versions (see below) | yes, index kept, type updated |
| `other mod` | its sector is in no game version: another mod adds it, and the file edits that mod's nodes (see below) | yes, kept as it is |
| `source mismatch` | the node matches neither the `--from` version nor the `--to` one at its index, and its debugName names no single other node: the file was made for another game version | commented out |

A node matches a version when the node at its index has the same type, the same actor / instance count
(`expectedActors`, `expectedInstances`) and, when the file gives one, a fitting debugName. Names are compared without
their numbering, case and braces, and with the file names of the node's resources: `drink_bottle_i_beer.022`
(a Blender object name) fits `drink_bottle_i_beer_022`, and a list of instance names (`can.001, can.002`) fits when
one of them does. A name that names no node of the sector, such as a generic `dirt.001`, is not compared.

### The output file

The output is the input file with the new values written in: its comments, order and layout stay, only the
indices, types and counts change. A `# Game version:` line gives the game version it is now made for: the line of
the input is updated where it is, anywhere in the comments that open the file (under a copyright, for example), or
added as the first line when the input has none. The next conversion reads it as the version to convert from:

```yaml
# Game version: 2.5
streaming:
```

- **Nodes left out** (`not found`, `source mismatch`) are commented out where they were, under a line that gives the
  reason. For a node not found, a second line tells what it was in the `--from` version, to look for it by hand in
  WolvenKit or in game: its type, debugName (or resource, when it has no name) and world position. For a node that
  lists actors / instances, the positions of those (up to 3) are given instead, as instanced nodes often sit at
  `[0, 0, 0]`.

  ```yaml
          # axl_sector_updater: not found in 2.5
          # axl_sector_updater: was in 2.31: worldStaticDecalNode 'decals_084' at [-1420.1196, 1272.4044, 127.5367]
          # - index: 41
          #   type: worldStaticDecalNode
  ```

- **Nodes found by name** (`by name`): an index can be wrong in the file (an entry copied from another one, or
  numbered by another tool) while its debugName is what the author saw. When the node at its index does not fit, the
  node of the `--from` sector that the name names is converted instead, under a comment that gives both:

  ```yaml
      # axl_sector_updater: found by its debugName (node #523 'drink_bottle_r_gusano.mesh' in 2.2): index 501 of the file is 'computer_panels_mlt_g.mesh'
      - index: 525
        type: worldInstancedMeshNode
        debugName: drink_bottle_r_gusano.mesh
  ```

  Best first: a node whose debugName is that name (Blender numbering `.001` read as `_001`), then a node whose
  resource file is, of the same type and actor / instance count; nodes listed by other entries of the file are
  skipped. A name that only shares its stem (`magazine_i.001` and `magazine_i_002`) is taken only when a single node
  of the sector has it. When several nodes fit equally, the node stays `source mismatch` and its comment lists them.
- **Files that mix versions**: a file edited in both versions (a 2.31 removal list completed in 2.5) has nodes that
  already point to the right `--to` node. They are kept at their index (`already target`), with the `--to` type
  (`worldCookedStaticDecalNode`…), and the others are converted.
- **Sectors of another mod**: a file can edit the nodes of a sector that another mod adds
  (`ef_coronado_house_additions\sectors\ef_coronado_house_additions.streamingsector`), which no game version has.
  Their indices only depend on that mod, not on the game version: these entries are kept as they are, with their
  `expectedNodes` (`other mod`), but nothing checks them. If that mod has changed since the file was made, take the
  new indices from its updated sector.
- **Nodes found in another sector** are commented out with `moved to <sector>` and added to that sector: to its
  entry in the file, or to a new entry at the end of the list. 2.5 moved some nodes to a sector of another grid
  level (`exterior_2_2_0_1` → `exterior_4_4_1_0`): they are found there too.
- **Actors and instances** (`actorDeletions`, `instanceDeletions`, `actorMutations`, `instanceMutations`) are found
  again one by one, by world position (within 1 cm), orientation and shape count / scale, and renumbered: several
  actors often share a position; `expectedActors` / `expectedInstances` get the new count. An
  actor / instance not found is commented out of its list. A node that would keep none of the actors / instances it
  lists is left out (`not found`): with an empty list, ArchiveXL would remove the whole node.
- **Actors and instances moved to another node**: 2.5 regrouped the actors of many collision nodes, sometimes into a
  node of another sector. The entry goes to the node that holds most of the actors it lists; the others are commented
  out of its list with `moved to node <index> of <sector>`, and added to the file as an entry for that node. Each
  source node keeps its own entries, even when several of them now point to the same node: ArchiveXL applies every
  actor / instance list of a node.
- **A node listed several times** (a removal and a mutation of the same node, two mutations…) keeps all its entries,
  converted to the same node.
- **JSON input** is converted to YAML the same way. An output named after a `.json` file becomes `.xl`.
- **`--format json`** writes JSON instead, which cannot hold comments: one entry per sector, sorted by path, nodes
  sorted by index, nodes left out removed.

Hand-edited files with tabs at the end of lines are accepted (they are removed from the output).

**Corrupted debugNames**: some tools write raw bytes (a memory address) as the debugName of nodes that have none in
the game, mostly `worldInstancedMeshNode` and `worldEntityProxyMeshNode`, which can make the file invalid UTF-8.
Such files are read anyway (with a warning), these names are not used to match nodes, and each is replaced by the
debugName of the node at its index in the `--from` version, or by `''` when it has none (the usual case). The console
gives their count, the report's `name_fix` column the old and new values.

### Options

Convert a single file or another folder by giving the input and output:

```
py -3.14 axl_sector_updater.py --from 2.31 --to 2.5 "IAC 2.31 - Fuel tank.xl" "IAC 2.5 - Fuel tank.xl"
py -3.14 axl_sector_updater.py --from 2.31 --to 2.5 "...\source\resources" "...\resources 2.5"
```

| Parameter | Purpose | Default |
|---|---|---|
| `input` | file or folder to convert (subfolders included) | `input` |
| `output` | output file or folder | `output` |
| `--from <version>` | game version the files were made for (`2.31`…), or path to a node export (zip or folder); a file whose first lines give its version (`# Game version: 2.31`) uses that one instead | required for the files without that line |
| `--force-from` | uses `--from` for every file, even those with a `# Game version:` line | off |
| `--to <version>` | target game version (`2.5`…), or path to a node export | required |
| `--format <yaml\|yml\|json>` | writes the content as YAML or JSON (see [The output file](#the-output-file)) | `yaml` |
| `--debug` | writes a `<name>.report.tsv` report next to each converted file (see below) | off |
| `--radius <m>` | maximum distance to accept a moved node whose resources are identical | `2` |
| `--versions-dir <folder>` | folder where node exports are kept and downloaded | `versions` |
| `--release-url <url>` | where missing node exports are downloaded from | `node-exports` release |

Accepted files: `.xl`, `.yaml`, `.yml`, `.json` (YAML or JSON content). Files without `streaming.sectors` are
skipped.

The `--debug` report opens in Excel / LibreOffice and details every node: `status`, `sector`, `kind`, `old_index`,
`old_type`, `debugName`, `new_sector` (empty if same sector), `new_index`, `new_type`, `distance_m`, `resources`
(Jaccard index of the resources), `elements` (share of actors / instances found), `actors` (remapped
actors / instances / total), `note`, `name_fix` (corrupted debugName replaced: old -> new value).

## FAQ

### Installing

**`pip` is not recognized as the name of a cmdlet…**
Don't type `pip` on its own. Use `py -3.14 -m pip install -r requirements.txt`, which works even when `pip` is not
on the PATH. If `py` is not recognized either, Python is not installed: see [Installation](#installation-windows).

**`Python was not found; run without arguments to install from the Microsoft Store…`**
Type `py -3.14 …` instead of `python …`, as in [Usage](#usage): `python` is often not set up even when Python is
installed, and Windows then shows this Microsoft Store shortcut. If `py` does not work either, Python is not
installed: see [Installation](#installation-windows).

**`py` is not recognized**
Python is not installed. Install it, then **close and reopen the terminal**.

**`No runtime installed that matches 3.14` / `No suitable Python runtime found`**
`py` cannot find Python 3.14. Run `py install 3.14`, then try again; if that fails with `can't open file
'…\install'`, see the next question. If you already have another Python between 3.10 and 3.14, `py -0` shows the
versions `py` knows: use that number instead of `3.14` in every command.

**`py install 3.14` fails with `can't open file '…\install'`**
You have the older Python launcher, which cannot install Python versions. Download the Python 3.14 installer from
[python.org](https://www.python.org/downloads/windows/) and run it instead.

**`Microsoft Visual C++ 14.0 or greater is required` / `Failed building wheel for PyYAML`**
Your Python is too recent (3.15). PyYAML, the library this tool uses, has no ready-made version for it yet, so pip
tries to build it and needs Microsoft's compilers. Install Python 3.14 (`py install 3.14`, or the installer from
[python.org](https://www.python.org/downloads/windows/)) and use the `py -3.14` commands above. You don't need to
uninstall 3.15.

**`ModuleNotFoundError: No module named 'yaml'`**
The library was installed for another Python than the one running the tool. Use the same version in both commands:
`py -3.14 -m pip install -r requirements.txt`, then `py -3.14 axl_sector_updater.py …`.

**`can't open file … axl_sector_updater.py` / `Could not open requirements file`**
The terminal is not in the tool folder. Open it from the folder (Installation, step 3).

### Running

**`no .xl file in …\input`**
The `input` folder is empty. Put your files in it, or give the file path in the command (see [Options](#options)).

**`download interrupted`, or the download is stuck**
Run the command again. You can also download the `nodes-<version>.zip` of both versions by hand from the
[`node-exports`](https://github.com/Akiway/ArchiveXL-Sector-Updater/releases/tag/node-exports) release and put
them, still zipped, in the `versions` folder.

**`no node export for game version …` / `neither a game version nor an existing node export`**
Check the version numbers: write `2.31` and `2.5`, not `2.3.1` or `v2.5`. Only these versions are available:
`1.63`, `2.02`, `2.13`, `2.2`, `2.21`, `2.31`, `2.5`.

**`the output would overwrite the input`**
Choose another output file or folder than the input.

**`no streaming sectors, skipped`**
Nothing to worry about: the file has no sector edits, so there is nothing to convert.

### Results

**Some nodes are `match` or `moved`**
They are converted, but not identical to the original: check in game that the right object is removed or changed.
`--debug` gives the details.

**Some nodes are `not found`**
The object no longer exists in the new version, or changed too much. It is commented out in the output file, with
its type, name and position in the old version (see [The output file](#the-output-file)): find its replacement in
WolvenKit or in game and add it by hand if you still need it.

**Some nodes are `source mismatch`, or the warning `expectedNodes differs from <version>`**
The file was made for another game version than `--from` (for example a 2.3 file converted with `--from 2.31`).
When the warning names the version the file was made for, run again with it (`--from`, or a
`# Game version: <version>` line at the top of the file). When it matches no exported version, that version is
not on the `node-exports` release (or not in your `versions` folder): these nodes cannot be converted
automatically. A few isolated `source mismatch` nodes in a file that otherwise converts usually have a
wrong index or name in the file: when their debugName names one node of the sector, they are converted as `by name`;
otherwise the comment lists the nodes the name could designate: check them by hand.

**Some nodes are `already target`**
The file was partly made for the `--to` version already: these nodes are kept at their index. Check them in game if
the file was not meant to mix versions.

**Some nodes are `other mod`**
They edit a sector that another mod adds, not the game: they are kept as they are (see
[The output file](#the-output-file)). They stay valid as long as that mod has not changed its sector.

**A converted collision removal does nothing in game**
Removing actors from 2.5 collision nodes needs an ArchiveXL newer than 1.28.1: see
[ArchiveXL compatibility](#archivexl-compatibility).

**I can't open the `nodes-*.zip` files**
You don't need to. To look inside anyway, use 7-Zip: Windows Explorer does not support their compression (LZMA).

## ArchiveXL compatibility

- The written types are those of the target version (`worldCookedStaticMeshNode`, `worldCookedCollisionNode`…):
  ArchiveXL requires the exact node type.
- `actorDeletions` on `worldCookedCollisionNode` (2.5 collisions): supported by ArchiveXL **after 1.28.1**.
- ArchiveXL ignores misspelled keys without warning (`instanceDeletion`, `sscale`…), and a deletion whose actor /
  instance list it cannot read removes the whole node. The tool does not check spelling: the console and the report
  only show the nodes it converted.

## Limitations

- The node export of the version **the `.xl` was made for** is required: a 2.3 `.xl` read as 2.31 yields
  `source mismatch`.
- Two identical nodes at the same place (game duplicates) are told apart by the closest index; this is noted in the
  console and the report.
- The node exports have no actor data for `worldFoliageNode`: its `actorDeletions` are kept as they are, and the
  node is reported as `match`.
- The instances of `worldInstancedDestructibleMeshNode` and `worldInstancedOccluderNode` are stored relative to their
  node: they only locate a node within `--radius` of the old one, and are never looked for in other nodes.

## Node exports (maintainers)

An export describes every sector of one game version: one `<sector path>.jsonl` per sector, one line per
`nodeData` entry (schema: [docs/NODE_RECORDS.md](docs/NODE_RECORDS.md)). The zip also holds a `manifest.json`
(game version, exe version, schema version, sector and record counts, unreadable game sectors); the script checks
the version and the schema before using it. Entries are LZMA-compressed: the script reads the zip in place, one
sector at a time, without unpacking it.

Publishing a new version (after a game patch):

1. Extract the sectors and build the export with the `sector_extract` tools (not part of this repository yet).
2. Pack it: `python pack_nodes.py "<export folder>" --version 2.6 --exe-version <exe FileVersion> --unreadable <unreadable.txt>`
   (writes `versions/nodes-2.6.zip`).
3. Publish it: `gh release upload node-exports versions/nodes-2.6.zip`.

The zips are not tracked in the repository (`.gitignore`): each version is an asset of the `node-exports` release.

## Copyright and license

Copyright © 2026 Aurélien "Akiway" Klein. All Rights Reserved.

This software is free to download and use for personal, non-commercial purposes only.

Modification, redistribution, commercial use, and reuse of the source code are prohibited without prior written permission.

See [LICENSE](LICENSE.md) for details.

Cyberpunk 2077 is a trademark of CD PROJEKT S.A. The node exports are derived from its game data.
