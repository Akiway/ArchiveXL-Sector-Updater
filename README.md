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

   `--from` is the game version your files were made for, `--to` the version to convert them to.
3. The converted files are in the `output` folder, with the same folder layout as `input`. They are YAML files with
   your comments kept (see [The output file](#the-output-file)).

The first run downloads the game data of both versions (`nodes-2.31.zip` and `nodes-2.5.zip`, ≈ 350 MB each) from
the [`node-exports`](https://github.com/Akiway/ArchiveXL-Sector-Updater/releases/tag/node-exports) release into the
`versions` folder. Later runs reuse them.

### Reading the result

While a file is converted, the console shows its progress (`INCF.xl: 523 / 1953 sectors (26%)`). Then it prints
how many nodes got each status and lists every node that is not `exact`.

| Status | Meaning | In the output |
|---|---|---|
| `exact` | found, identical (same type, resources, position within 1 cm, every actor / instance) | yes |
| `match` | found, nearly identical (within 5 cm, or only part of its actors / instances found) | yes, **check it in game** |
| `moved` | same object, moved by more than 5 cm (up to `--radius`, 2 m by default) | yes, **check it in game** |
| `not found` | no such object in the new version, neither in the sector, nor in its 26 neighbours, nor in the sectors of the other grid levels around it, or none of the actors / instances it lists | commented out |
| `source mismatch` | the file does not match the `--from` version: it was made for another game version | commented out |

### The output file

The output is the input file with the new values written in: its comments, order and layout stay, only the
indices, types and counts change.

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
| `--from <version>` | game version the files were made for (`2.31`…), or path to a node export (zip or folder) | required |
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
actors / instances / total), `note`.

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
Run the command again. You can also download `nodes-2.31.zip` and `nodes-2.5.zip` by hand from the
[`node-exports`](https://github.com/Akiway/ArchiveXL-Sector-Updater/releases/tag/node-exports) release and put
them, still zipped, in the `versions` folder.

**`no node export for game version …` / `neither a game version nor an existing node export`**
Check the version numbers: write `2.31` and `2.5`, not `2.3.1` or `v2.5`. Only the versions listed on the
`node-exports` release are available.

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

**Some nodes are `source mismatch`, or the warning `it was not made for this source version`**
The file was made for another game version than `--from` (for example a 2.3 file converted with `--from 2.31`).
Run again with the right version. If that version is not on the `node-exports` release, these nodes cannot be
converted automatically.

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
