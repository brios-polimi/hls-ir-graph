# Artifact contract

Each project/top-function run produces:

- `<top>.ll`: graphable textual LLVM;
- `<top>.pragmas.log`: JSON Lines directive records or a native compiler dump;
- `<top>.compiler.log`: frontend diagnostics;
- `<top>.json`: ProGraML graph with schema-versioned enrichment;
- `<top>.provenance.json`: backend, frontend fidelity, compiler, and commands.

The Bambu frontend additionally writes `<top>.debug.ll` (unoptimized frontend
LLVM with source debug information) and `<top>.types.json` (version 1 debug type
table). Provenance links both files. The table applies to the frontend snapshot,
before configured IR transforms, and includes the raw debug LLVM's SHA-256.
See [DEBUG_TYPE_RECOVERY.md](DEBUG_TYPE_RECOVERY.md) for coverage and failure rules.

Directive semantics are never inferred from spelling alone:

- `source_requested`: active preprocessed source contains a request;
- `compiler_reported`: a backend frontend recognized it;
- `ir_realized`: backend IR contains an exact carrier or transformation;
- `report_realized`: a synthesis report confirms the behavior.

Bambu portable LLVM is classified `portable_clang_approximation` because PandA
plugins such as CSROA and `dumpBambuIrSSACpp` are not applied. Vitis uses its
backend frontend and compiler pragma dump.

Schema changes must increment the relevant version and document migration for
`wa-hls4ml-ingest` and `hls-surrogate-lab`.
