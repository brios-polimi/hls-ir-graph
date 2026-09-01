# Reproducing Bambu project preprocessing

## One-command smoke test

From this repository:

```bash
scripts/reproduce_bambu_preprocessing.sh
```

The script:

1. reads clock/part context from one retained wa-hls4ml config;
2. reports that its absolute `KerasModel` dependency is missing;
3. writes a deterministic minimal project with the local hls4ml Bambu branch
   through the `bambu-env` environment;
4. compiles the generated project with Clang 16 and Bambu synthesis defines;
5. captures active HLS source directives;
6. runs LLVM-16 ProGraML and common graph enrichment;
7. writes everything under a fresh `/tmp/hls4ml-bambu-preprocess.*` directory.

It does not modify `data/source`, registry state, or `hls-surrogate-lab` results.

Pass a different reference config and output root as positional arguments:

```bash
scripts/reproduce_bambu_preprocessing.sh \
  /absolute/path/to/hls4ml_config.yml \
  /tmp/my-bambu-preprocessing-run
```

The reusable project-only command is:

```bash
PYTHONPATH=src \
/home/brend/anaconda3/bin/conda run -n pipeline-env --no-capture-output \
python -m hls_ir_graph \
  /path/to/written/project /path/to/artifacts \
  --backend bambu --clang /usr/bin/clang-16
```

## Actual Bambu synthesis smoke test

To verify the written project with Bambu itself:

```bash
scripts/run_bambu_synthesis_smoke.sh \
  /path/to/written/project /tmp/bambu-synthesis-smoke
```

This resolves `which bambu`, extracts the AppImage to a temporary directory,
sets its runtime/compiler environment, runs synthesis, and removes only that
temporary extraction. An already extracted or mounted AppImage root can be
passed as the third argument to avoid extraction.

In this environment, `bambu --appimage-mount` could not mount because FUSE is
unavailable. `--appimage-extract` was the reproducible fallback. When invoking
the extracted `usr/bin/bambu` directly, both the AppImage library paths and its
`usr/compilers/clang-16/bin` must be used; otherwise Bambu can accidentally load
its plugins into Ubuntu Clang 16 and fail with undefined LLVM symbols.

## Verified result (2026-08-26)

Historical result below used the old `-O2` portable frontend. The current
frontend captures debug types before LLVM passes, so these graph counts are
not expected to match. See [DEBUG_TYPE_RECOVERY.md](DEBUG_TYPE_RECOVERY.md).

The preprocessing run completed with:

- 566 LLVM lines using the portable subset of Bambu's optimization flags;
- 54 active HLS directives, including 2 `HLS_interface` records;
- 1,110 enriched graph nodes and 2,483 links;
- 2 ProGraML functions after Bambu-like inlining;
- zero hierarchy mapping failures.

Counts are diagnostic, not golden tests: compiler flags and hls4ml templates
legitimately change them.

The actual Bambu run also completed. For the deterministic smoke model it
reported 6 minimum/maximum cycles, 18 estimated DSPs, and 1,079 flip-flops.
With `--no-clean`, Bambu retained `myproject.cpp.bambuir` and
`architecture.xml`; it did not retain usable textual LLVM (`first_opt.ll` and
`second_opt.ll` were empty). This is why the current graph artifact is marked
`portable_clang_approximation` rather than backend-faithful Bambu IR.

## What the reference config can and cannot do

`hls4ml_config.yml` serializes conversion settings, but its model entry is an
external tagged reference. It can reload a model only while that referenced
file and required Python custom objects remain available. Legacy compact
wa-hls4ml snapshots retain generated firmware and YAML but not the referenced
Keras model. `wa-hls4ml-ingest` now retains a minimal compressed, checksummed
model-IR bundle for newly downloaded projects without rewriting legacy state.

The smoke generator therefore uses the retained YAML only for contextual
clock/part values and creates a small deterministic model. It does not claim to
regenerate the selected legacy archived design. Future bundled snapshots can be
materialized into a separate regenerable project by the ingestion repository.
