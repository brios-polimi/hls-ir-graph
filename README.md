# hls-ir-graph

Backend-aware preprocessing for written C/C++ HLS projects. It emits textual
LLVM, explicit directive/provenance sidecars, and enriched ProGraML JSON without
depending on a particular dataset, archive layout, label source, or ML model.

```bash
pip install -e .
hls-project-to-graph PROJECT_DIR OUTPUT_DIR --backend bambu
```

The initial adapters are:

- **Vitis**: Vitis Clang synthesis IR and compiler-reported pragma enrichment.
- **Bambu**: portable Clang-16 LLVM using Bambu-compatible flags. Provenance
  explicitly records that Bambu's custom PandA plugins are not represented.

## Package structure

```text
src/hls_ir_graph/
  frontends/   backend compilation adapters and LLVM cleanup policy
  graph/       ProGraML conversion, hierarchy, directives, relations
  config.py    dataset-independent tool configuration
  pipeline.py  public staged and end-to-end APIs
  cli.py       project-oriented CLI
```

Frontend adapters and graph transforms are separate so a backend can add
post-frontend LLVM processing without coupling it to graph serialization.
Dataset ingestion belongs in `wa-hls4ml-ingest`; training and augmentation
belong in `hls-surrogate-lab`.

Configured compiler/IR transforms are registered under `transforms/` and saved
in provenance; see [docs/EXTENDING.md](docs/EXTENDING.md). A reproducible Bambu
hls4ml generation and preprocessing smoke test is provided by
`scripts/reproduce_bambu_preprocessing.sh` and documented in
[docs/BAMBU_PREPROCESSING_SMOKE_TEST.md](docs/BAMBU_PREPROCESSING_SMOKE_TEST.md).

```bash
PYTHONPATH=src /home/brend/anaconda3/bin/conda run -n pipeline-env \
  --no-capture-output python -m unittest discover -s tests -v
```
