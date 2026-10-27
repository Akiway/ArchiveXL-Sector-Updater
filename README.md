# ArchiveXL Sector Updater

Updates the sector node removals (`nodeDeletions`) and mutations (`nodeMutations`) of an ArchiveXL `.xl` file from
one Cyberpunk 2077 version to another, notably from 2.31 to 2.5.

In 2.5, CDPR batched static meshes, decals, collisions, proxies… into "cooked" nodes and reordered `nodeData`: the
indices of a 2.31 `.xl` now point to other objects. This script finds every node by what it **is** (node family,
resources, world position, debugName, actor / instance positions), then rewrites its index, its type,
`expectedNodes` and the `actorDeletions`.

## Requirements

Python 3.10+ and PyYAML: `pip install -r requirements.txt`.

## Folders

| Folder | Purpose |
|---|---|
| `input/` | files to convert, used when no input is given (subfolders included) |
| `output/` | converted files (and reports with `--debug`), used when no output is given; mirrors the input tree |
| `versions/` | node exports, one `nodes-<version>.zip` per game version, downloaded automatically when needed |

## Usage

Put the `.xl` files in `input/`, then run:

```
python axl_sector_updater.py --from 2.31 --to 2.5
```

The converted files are written to `output/`. On the first run, `nodes-2.31.zip` and `nodes-2.5.zip` (≈ 350 MB
each) are downloaded from the
[`node-exports`](https://github.com/Akiway/ArchiveXL-Sector-Updater/releases/tag/node-exports) release into
`versions/`.

Explicit input and output (file or folder):

```
python axl_sector_updater.py --from 2.31 --to 2.5 "IAC 2.31 - Fuel tank.xl" "IAC 2.5 - Fuel tank.xl"
python axl_sector_updater.py --from 2.31 --to 2.5 "...\source\resources" "...\resources 2.5"
```

| Parameter | Purpose | Default |
|---|---|---|
| `input` | file or folder to convert (subfolders included) | `input/` |
| `output` | output file or folder | `output/` |
| `--from <version>` | game version the files were made for (`2.31`…), or path to a node export (zip or folder) | required |
| `--to <version>` | target game version (`2.5`…), or path to a node export | required |
| `--format <yaml\|yml\|json>` | writes the content as YAML (`yaml` or `yml`) or JSON; the file extension is kept | input format |
| `--debug` | writes a `<name>.report.tsv` report next to each converted file (see below) | off |
| `--radius <m>` | maximum distance to accept a moved node whose resources are identical | `2` |
| `--versions-dir <folder>` | folder where node exports are kept and downloaded | `versions/` |
| `--release-url <url>` | where missing node exports are downloaded from | `node-exports` release |

YAML or JSON input (`.xl`, `.yaml`, `.yml`, `.json`). Files without `streaming.sectors` are skipped.

## Statuses

Every node is classified by how it was found in the target version:

| Status | Meaning | Written to the output |
|---|---|---|
| `exact` | same family, same resources, same position (≤ 1 cm) and orientation; for nodes with elements, every actor / instance found | yes |
| `match` | same node within 5 cm, or at least half of the actors / instances found, or removed actors not found | yes, **check it** |
| `moved` | same resources, moved by more than 5 cm (≤ `--radius`) | yes, **check it** |
| `not found` | no candidate in the target version, neither in the sector nor in its 26 neighbours | no |
| `source mismatch` | the `.xl` does not describe the node at this index in the source version (different type, debugName or actor count): the file was made for another version | no |

The console prints the count per status and lists everything that is not `exact` or carries a note (equivalent
candidates, node moved to a neighbouring sector, actors not found).

With `--debug`, the `<name>.report.tsv` report details every node (opens in Excel / LibreOffice): `status`,
`sector`, `kind`, `old_index`, `old_type`, `debugName`, `new_sector` (empty if same sector), `new_index`,
`new_type`, `distance_m`, `resources` (Jaccard index of the resources), `elements` (share of actors / instances
found), `actors` (remapped actorDeletions / total), `note`.

## Node exports

An export describes every sector of one game version: one `<sector path>.jsonl` per sector, one line per
`nodeData` entry (schema: [docs/NODE_RECORDS.md](docs/NODE_RECORDS.md)). The zip also holds a `manifest.json`
(game version, exe version, schema version, sector and record counts, unreadable game sectors); the script checks
the version and the schema before using it.

Entries are LZMA-compressed: the script reads the zip in place, one sector at a time, without unpacking it. To
open it by hand, use 7-Zip (Windows Explorer does not support LZMA).

Publishing a new version (after a game patch):

1. Extract the sectors and build the export with the `sector_extract` tools (not part of this repository yet).
2. Pack it: `python pack_nodes.py "<export folder>" --version 2.6 --exe-version <exe FileVersion> --unreadable <unreadable.txt>`
   (writes `versions/nodes-2.6.zip`).
3. Publish it: `gh release upload node-exports versions/nodes-2.6.zip`.

The zips are not tracked in the repository (`.gitignore`): each version is an asset of the `node-exports` release.

## ArchiveXL compatibility

- The written types are those of the target version (`worldCookedStaticMeshNode`, `worldCookedCollisionNode`…):
  ArchiveXL requires the exact node type.
- `actorDeletions` on `worldCookedCollisionNode` (2.5 collisions): supported by ArchiveXL **after 1.28.1**
  (`main` branch, commit "Support new world nodes" of 2026-10-27). With 1.28.0 / 1.28.1, such a node is ignored or
  removed entirely. Removing individual shapes (sub-elements) is not supported in 2.5.

## Limitations

- The export of the version **the `.xl` was made for** is required: a 2.3 `.xl` read as 2.31 yields
  `source mismatch` (see above).
- Two identical nodes at the same place (game duplicates) are told apart by the closest index; this is noted in the
  console and the report.
- Element mutations (actor / instance positions) are not remapped, only the node index and type are.

## Validation (2.31 → 2.5)

- 14 `.xl` files from Improved Aldecaldos Camp, 156 nodes (151 removals, 5 mutations): all `exact`.
- Independent check against the 2.5 game archives (WolvenKit 10): correct `expectedNodes` and types, the resource of
  every new index matches the original debugName, same collision actor counts.
- 2.3 `.xl` files read as 2.31: the nodes that do not match are rejected (`source mismatch`).

## Copyright and license

Copyright © 2026 Aurélien "Akiway" Klein. All Rights Reserved.

This software is free to download and use for personal, non-commercial purposes only.

Modification, redistribution, commercial use, and reuse of the source code are prohibited without prior written permission.

See [LICENSE](LICENSE.md) for details.

Cyberpunk 2077 is a trademark of CD PROJEKT S.A. The node exports are derived from its game data.
