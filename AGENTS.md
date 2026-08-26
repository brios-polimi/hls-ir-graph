# hls-ir-graph

## Purpose

This repository converts an already-written HLS C/C++ project into versioned
LLVM, directive, provenance, and ProGraML artifacts. It is dataset-neutral and
training-neutral.

## Start here

- `src/hls_ir_graph/pipeline.py`: staged public API and artifact contract.
- `src/hls_ir_graph/config.py`: external tool and frontend configuration.
- `src/hls_ir_graph/frontends/`: Vitis/Bambu compilation adapters.
- `src/hls_ir_graph/graph/`: ProGraML and enrichment implementation.
- `docs/ARTIFACT_CONTRACT.md`: semantic and provenance rules.
- `docs/EXTENDING.md`: backend and post-frontend transform extension points.

## Boundaries

- Do not add Hugging Face URLs, archive numbering, registries, manifests,
  dataset labels, tensorization, or training code.
- Backend-specific compiler behavior belongs in `frontends/`; common LLVM
  cleanup must state whether it is semantic or an intentional ablation.
- Never describe source directives as realized hardware behavior without
  backend evidence. Preserve `source_requested`, `compiler_reported`, and
  `report_realized` distinctions.
- Keep generated `.ll`, graph JSON, compiler logs, and HLS projects outside the
  repository or under ignored `artifacts/`.

## Testing

Use `/home/brend/anaconda3/bin/conda run -n pipeline-env --no-capture-output
python -m unittest discover -s tests -v`. Use small temporary projects only;
do not synthesize or download datasets in unit tests.
