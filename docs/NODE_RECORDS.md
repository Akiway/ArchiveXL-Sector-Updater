# Node records (targeted export)

One `<sector path>.jsonl` file per sector, one JSON line per `nodeData` entry, in the order of the indices used by
ArchiveXL (`nodeDeletions[].index`). The schema is the same for every game version, so that indices can be mapped
from one version to another without WolvenKit.

| Key | Content | 2.31 (`node_export`, WolvenKit 8.16.1) |
|---|---|---|
| `i` | `nodeData` index | position in `worldStreamingSector.nodeData` |
| `n` | node index | `worldNodeData.NodeIndex` |
| `t` | node class | RTTI type of the node (`worldStaticMeshNode`…) |
| `d` | debugName | `worldNode.debugName` (`null` if empty) |
| `id` | `nodeData` identifier (string, uint64) | `worldNodeData.Id` |
| `p` | world position `[x, y, z]`, 4 decimals | `worldNodeData.Position` |
| `q` | orientation `[i, j, k, r]`, 5 decimals | `worldNodeData.Orientation` |
| `s` | scale `[x, y, z]`, 5 decimals | `worldNodeData.Scale` |
| `r` | `{RED property path: value}`: every non-empty resource path (DepotPath) and appearance name of the node, handles and arrays included | walk of the `[RED]` properties; skipped: `compiledData`, `buffer`, `worldTransformsBuffer`, `cookedInstanceTransforms`, `instanceData`. Unresolved path = decimal hash |
| `e` | elements that can be removed one by one (absent for other nodes) | see below |

`e` per class (2.31):
- `worldCollisionNode` (and `worldFoliageDestructionNode`): one element per actor
  `[x, y, z, qi, qj, qk, qr, shape count]`, world position (FixedPoint / 2^17).
- `worldInstancedMeshNode`: one instance `[tx, ty, tz, qi, qj, qk, qr, sx, sy, sz]` (world transforms of the shared
  buffer, `startIndex` / `numElements` slice).
- `worldInstancedDestructibleMeshNode`: one instance `[x, y, z, qi, qj, qk, qr]`, as stored (relative to the node:
  ArchiveXL applies `node transform × inverse`).
- `worldInstancedOccluderNode`: one instance `[x, y, z]` (translation row of the matrix).

## 2.5 variant (`node_export25`, WolvenKit 10)

`nodeData` becomes a `worldNodeDataBuffer` of `worldCompiledNodeInstanceSetupInfo`; "cooked" nodes batch several
former nodes, and a record designates `InnerNodes[InstanceIndex]` (`CollisionNodes[...]` for collisions).
- `n` = `NodeIdx`; `k` = `InstanceIndex`, present only for cooked nodes (including `worldInstancedMeshNode`);
- `t` = 2.5 class: the mapping has to translate the families (`worldStaticMeshNode` ↔ `worldCookedStaticMeshNode`,
  `worldStaticDecalNode` ↔ `worldCookedStaticDecalNode`, `worldCollisionNode` ↔ `worldCookedCollisionNode`,
  `worldGenericProxyMeshNode` ↔ `worldCookedGenericProxyMeshNode`…);
- `d` = debugName of the node; cooked nodes have none (`"None"`);
- `id` = `GlobalNodeId.Hash`: **always 0 in 2.5 files** (filled in by the game at load time), unusable;
- `p`/`q`/`s` = `Transform` / `Scale` of the setup info;
- `r` = resources of the node, then of the inner node. CNames in cooked buffers are FNV1a64 hashes:
  `collect_names.py` lists the known (2.31) appearance names to resolve them; an unknown hash stays decimal;
- `e` = actors of the collision inner node (world position, `ShapeCount`), world transforms of `TransformRanges[k]`
  for instanced meshes, `instanceTransforms` for destructibles (same relative convention as 2.31), unchanged buffer
  for occluders.

Cross-check on 71 sectors whose node count did not change (17,295 records): same position at the same index 92%,
same resources 92%, first element at the same world position for 92% of instanced meshes, 97% of collisions, 100%
of destructibles, 93% of occluders. The rest are nodes that were really reordered.

---

Copyright © 2026 Aurélien "Akiway" Klein.
