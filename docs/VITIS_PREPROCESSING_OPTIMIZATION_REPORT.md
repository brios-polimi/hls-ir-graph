# Vitis preprocessing optimization report

## Scope and benchmark

This review covers the Vitis path from retained hls4ml source through Vitis
LLVM emission, ProGraML conversion, graph enrichment, JSON serialization, and
`hls-surrogate-lab` tensorization. Bambu behavior was not changed.

Measurements used temporary copies of one real `3layer/archive_19` project
(`5904c91b-bdc1-49bb-a5f2-232eec171fc9`) under `/tmp`. The source graph in
`data/` was read but never modified. Timings are single warm local runs and are
useful as proportions, not general throughput claims.

| Artifact | Bytes | Nodes | Serialized edges | stdlib JSON load |
|---|---:|---:|---:|---:|
| Existing graph | 850,846 | 2,660 | 6,000 | 4.81 ms |
| New schema, full text | 652,252 | 2,371 | 4,193 | 3.42 ms |
| New schema, compact Vitis mode | 467,425 | 2,371 | 4,193 | 2.91 ms |

The compact result is 45.1% smaller than the existing artifact. Part of that
total comes from the ProGraML SSA-variable deduplication done alongside this
review. On the original graph in isolation, `contains` links occupied 96,506
bytes (11.3%), zero-valued `position` fields occupied 68,185 bytes (8.0%), and
instruction/variable `full_text` occupied 154,323 bytes (18.1%). On the new
schema, compacting full text and repeated node metadata reduced 652,252 bytes to
467,425 bytes (28.3%).

The copied project took 2.89 seconds and 126 MB peak RSS for Vitis compilation,
then 1.47 seconds and 65 MB peak RSS for graph construction/enrichment. The
compact graph still tensorized successfully with one derived block membership
per instruction and one derived function membership per block.

## Changes made

- Hierarchy schema 3 stores ownership in node `function`/`block` fields and no
  longer constructs or serializes redundant membership links.
- `hls-surrogate-lab` derives tensor-only containment directly from those node
  fields. Serialized containment is invalid in the new schema.
- Relation schema 2 omits `position` when it is zero. Consumers already use zero
  as the default.
- Vitis dataset ingestion now defaults to compact graph storage. Pragma
  placement and cleanup still see complete text; final serialization retains
  `full_text` only for scalar constants required by literal tensor features.
  It also removes repeated per-node injector/hierarchy schema markers whose
  values are recorded at graph level. Standalone `hls-ir-graph` retains full
  text by default for visualization.
- The two cleanup passes over the potentially large preprocessed C++ file now
  share one read and at most one write.
- The Vitis compiler-version probe now uses the required vendor library path
  and is cached per worker. Previously it launched once per project and returned
  no version on this machine because the Clang executable could not load Boost.

## Further worthwhile changes

### Compressed graph artifacts

The compact sample is 467,425 bytes as JSON and 41,891 bytes with gzip level 6,
a further 91.0% reduction. Transparent `.json.zst` or `.json.gz` support is the
largest remaining storage opportunity. It should be introduced across graph
discovery, manifests, visualization, and tensorization together. Zstandard is
the preferred candidate because decompression is generally fast; benchmark it
on a representative archive before choosing a level. This was not changed here
because it alters the artifact/container contract rather than only graph
semantics.

### Stream the ProGraML conversion

`graph_via_binary()` currently captures the complete protobuf output and then
the complete JSON output in memory. Connecting `llvm2graph` to `graph2json` as a
stream and writing graph JSON directly would reduce per-worker peak memory for
large graphs. The measured sample peaked at only 65 MB, so this is lower priority
than compression and should first be measured on the largest Vitis kernels.

### Move symbolic pragma resolution earlier

When numeric pragma arguments remain symbolic, graph enrichment launches an
additional Vitis preprocess and LLVM-emission pair. The main compilation has
already performed similar work, but its temporary preprocessed source is gone.
A future design could resolve and record these values while the main Vitis
temporary directory is alive. This could remove two compiler processes for
affected projects, but it couples compiler and pragma-enrichment stages and
needs coverage measurements before implementation.

### Retained LLVM policy

The copied textual LLVM was 291 KB. `delete_ll_after_graph=true` saves this space
when source plus toolchain provenance is considered sufficient, but retained
LLVM makes regraphing dramatically cheaper and more reproducible than
recompilation. Keeping the current default is appropriate unless disk pressure
outweighs regraphing needs; compressed LLVM is a better compromise.

### Tensor-only hierarchy representation

Containment remains in `.pt` tensors because generic heterogeneous models
message-pass over it. Hierarchical models only need owner vectors. A
model-specific tensor format could replace each 2-by-N containment edge index
with one N-element owner vector, roughly halving hierarchy-index storage and
transfer, but it complicates PyG batching and does not improve JSON generation.
It is unlikely to matter as much as the model's instruction/def-use message
passing.

## Practices already appropriate

- Graph JSON uses compact `orjson` serialization when available.
- Numeric pragma probes are already batched into one compilation per project.
- Vitis projects are processed in bounded persistent worker processes, allowing
  source preparation and compilation to overlap without an unbounded disk queue.
- Source retention keeps a compact recoverable project rather than the complete
  extraction cache.
