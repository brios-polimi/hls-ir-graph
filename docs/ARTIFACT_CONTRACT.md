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

Hierarchy schema version 3 records ownership only in node fields: instructions
and blocks carry `function` and `block`, while function nodes carry `function`.
Redundant `contains` links are not serialized. Relation schema version 2 also
omits edge `position` when its value is zero; consumers treat a missing position
as zero. `hls-surrogate-lab` derives tensor-only containment relations from the
node ownership fields for models that message-pass over them.

Schema changes must increment the relevant version and document migration for
`wa-hls4ml-ingest` and `hls-surrogate-lab`.
