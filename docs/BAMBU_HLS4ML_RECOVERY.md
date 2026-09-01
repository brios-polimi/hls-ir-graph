# Reproduce an hls4ml Bambu project

The archive needs only a written `hls4ml_config.yml` and a
`model_ir/keras_model.h5` or `model_ir/keras_model.keras` file (optionally gzip
compressed). The recovery script
loads the model (including QKeras custom objects), changes the conversion
backend to `Bambu`, calls `model.write()`, and then runs the `hls-ir-graph`
Bambu frontend on the newly written project.

```bash
cd /home/brend/projects/hls-ir-graph
scripts/reproduce_bambu_hls4ml_project.sh \
  /path/to/archive-project
```

The default output is a temporary directory under `/tmp`; pass a second
argument to retain it at a chosen location. It contains the decompressed model,
the source YAML, `hls4ml-project/`, `graph-artifacts/`, and a small
`recovery.json` summary. Nothing is written under the repository’s `data/`
tree.

The frontend can use the Bambu-bundled Clang when an extracted AppImage root is
available:

```bash
BAMBU_ROOT=/path/to/squashfs-root \
  scripts/reproduce_bambu_hls4ml_project.sh ARCHIVE_PROJECT /tmp/bambu-run
```

In this environment `bambu --appimage-mount` is unavailable because FUSE is not
present. The equivalent local fallback is
`bambu --appimage-extract`, followed by setting `BAMBU_ROOT` to the extracted
`squashfs-root` directory.

The graph frontend’s provenance intentionally labels its output
`portable_clang_approximation`: it captures source directives and emits LLVM,
but does not claim that Bambu’s internal PandA lowering is represented.

## Debug-derived type table (default)

No compiler rebuild or Bambu plugin is needed. The portable Clang frontend now
emits full debug information with LLVM passes disabled, recovers the names of
identified record types, and only then strips metadata for ProGraML. Each run
writes `myproject.debug.ll`, `myproject.types.json`, and the renamed `myproject.ll`.

The table records original/emitted LLVM names, full source type names, debug
IDs, mapping evidence, conflicts, and coverage. Incomplete mappings of emitted
AC record types fail by default; `bambu.require_complete_ac_types: false` in a
config file explicitly permits an incomplete exploratory result. See
[DEBUG_TYPE_RECOVERY.md](DEBUG_TYPE_RECOVERY.md) for the exact coverage scope.

The old `--architecture-xml`, `bambu.architecture_xml`, and unused
`bambu.mirror_optimization_flags` options have been removed. Interface-only XML
renaming is no longer part of preprocessing. Existing LLVM/graphs must be
regenerated to gain these names; existing artifacts are not edited in place.

## Optional native Bambu metadata

The Bambu Clang plugin stack can emit its metadata without running HLS:

```bash
scripts/generate_bambu_frontend_metadata.sh \
  /path/to/hls4ml-project /tmp/bambu-metadata "$BAMBU_ROOT"
```

This writes `panda-temp/architecture.xml` and
`panda-temp/myproject.cpp.bambuir`. The former contains source-level interface
types such as `ac_fixed<16, 6>`; the latter is Bambu’s internal line-oriented
IR dump. FUSE is not required when an extracted AppImage root is supplied.

These artifacts remain useful for studying Bambu itself, but are not needed
for debug-derived LLVM type recovery.

To run Bambu itself after recovery, source the extracted AppImage environment
and run from the generated project directory:

```bash
source "$BAMBU_ROOT/usr/settings.sh"
cd /tmp/bambu-run/hls4ml-project
bambu firmware/myproject.cpp --top-fname=myproject -lm \
  -Ifirmware/ac_types --compiler=I386_CLANG16 \
  -ftemplate-depth=2048 --generate-interface=INFER -v2 -m64
```

This produces Bambu’s `myproject.v`, `bambu_results.xml`, and internal
`panda-temp/` artifacts under the temporary run directory.
