# Compilation audit

## Follow-up: debug type recovery and flag reduction (2026-08-31)

The historical audit below describes an earlier pipeline. Current changes were
checked on one written Vitis source from each of the selected 2layer and conv2d
archives, with all experimental outputs outside the dataset.

| Source ID | Baseline normalized LLVM lines | Reduced flags | Project AP headers first |
| --- | ---: | --- | --- |
| `0162624c-ca32-4e05-a8be-48c9d355ef4b` (2layer) | 3,221 | Identical LLVM | Preprocessing error |
| `00dc820c-b275-4dcd-b439-64e13df52c55` (conv2d) | 37,329 | Identical LLVM | Preprocessing error |

The comparison applies the same existing weight/metadata cleanup and ignores
only ModuleID/source-filename paths. The reduced Vitis arguments keep `-fhls`,
the FPGA target, `-fno-threadsafe-statics`, `__VITIS_HLS__`, and the vendor header
include. `-fhls` itself defines `AESL_SYN`, `__SYNTHESIS__`, and `__HLS_SYN__` and
disables exceptions. It does not define `__VITIS_HLS__`, so that backend marker
is retained for source conditionals even though these samples do not need it.

Removed: redundant synthesis macro definitions, `-fno-exceptions`,
`-fno-math-errno`, `-fno-use-cxa-atexit`, `ap_sysc`/project `ap_types` includes,
and the forced `autopilot_ssdm_op.h` include. The old eight-family audit also
found the removed includes/defines redundant; the static-guard flag is retained
because that audit showed a real conv2d difference.

**Do not put the archived open-source AP headers first for Vitis HLS.** Both
selected projects contain an explicit `#error` in `ap_types/ap_common.h`:
"The open-source version of AP types does not support synthesis." The vendor
autopilot headers provide the compiler's synthesis implementation. Header
precedence is a semantic choice, not a portability cleanup.

Bambu no longer mirrors a subset of HLS `-O2` flags. It captures unoptimized
LLVM/debug associations, recovers record names, then builds graphs; see
[DEBUG_TYPE_RECOVERY.md](DEBUG_TYPE_RECOVERY.md). No Vitis optimization level
or weight policy was changed in this follow-up.

Local evidence: `/tmp/hls-debug-vitis-audit/report.json`, with per-variant LLVM
and diagnostics in the same directory. These samples validate the stated
changes, not arbitrary projects or header versions.

## Historical audit

This document records a bounded audit of `pipeline/compiler.py`. It is intended
to make compilation choices explicit before changing the generated LLVM dataset.
The audit used one explicitly selected retained source snapshot from each kernel
family. It did not scan `data/` recursively and wrote all ablation outputs to a
temporary directory.

## Method

The baseline was the checked-in `config.json` and the current compiler path:

1. Vitis clang preprocessing with `_vitis_args()`.
2. Removal of the unsupported `ROM_nP_BRAM` pragma and generated weight
   initializers.
3. Vitis clang textual LLVM emission.
4. `llvm.fpga.*` spelling normalization, static-initializer collapsing, and
   metadata removal.
5. The configured `sroa,mem2reg` LLVM pass pipeline.

IR comparisons ignored only temporary `ModuleID` and source-path noise plus the
metadata that this pipeline intentionally strips. A result marked `EQUIV` had an
identical normalized `.ll` file; raw byte hashes can differ because temporary
paths are embedded in LLVM output.

Selected datapoints:

| Family | Source project |
|---|---|
| `2layer` | `4506c86a-206d-49a4-bb76-41573720824e` |
| `3layer` | `5904c91b-bdc1-49bb-a5f2-232eec171fc9` |
| `conv1d` | `02d683c1-c6f0-4fcc-bbf5-85b9248f9756` |
| `conv2d` | `03d93e71-d950-4ca8-9dfe-00859064a528` |
| `dense_latency` | `d5cece7c-6cd2-4a8c-8eb6-901ccff48ac1` |
| `dense_resource` | `a02586db-5ce0-4544-ad42-b5e9ecd1a2e9` |
| `exemplar` | `d1b35da2-8c73-4ae5-ae14-eace77e749d8` |
| `rule4ml` | `12fd912bbec17caf6091f44e11652766` |

## Compilation argument ablation

The following matrix removes the named group from both preprocessing and LLVM
emission. `FAIL` means Vitis clang rejected the compilation; `DIFF` means it
completed but produced non-equivalent normalized IR.

| Family | Remove all `-fno-*` | Remove all explicit HLS defines | Remove `ap_sysc` include | Remove project `ap_types` include | Remove forced header | Remove target | Remove `-fhls` |
|---|---|---|---|---|---|---|---|
| `2layer` | EQUIV | EQUIV | EQUIV | EQUIV | EQUIV | DIFF | FAIL |
| `3layer` | EQUIV | EQUIV | EQUIV | EQUIV | EQUIV | DIFF | FAIL |
| `conv1d` | EQUIV | EQUIV | EQUIV | EQUIV | EQUIV | FAIL | FAIL |
| `conv2d` | DIFF | EQUIV | EQUIV | EQUIV | EQUIV | FAIL | FAIL |
| `dense_latency` | EQUIV | EQUIV | EQUIV | EQUIV | EQUIV | DIFF | FAIL |
| `dense_resource` | EQUIV | EQUIV | EQUIV | EQUIV | EQUIV | DIFF | FAIL |
| `exemplar` | EQUIV | EQUIV | EQUIV | EQUIV | EQUIV | FAIL | FAIL |
| `rule4ml` | EQUIV | EQUIV | EQUIV | EQUIV | EQUIV | DIFF | FAIL |

Interpretation:

- `-fhls` is required by this Vitis clang. Removing it allows preprocessing in
  some cases but LLVM emission fails in Vitis headers, including missing HLS
  builtins.
- The Vitis target is required. Removing it either changes the generated IR or
  fails in target-specific HLS headers.
- The explicit HLS macros and both `ap_sysc`/project `ap_types` include paths
  were redundant for all eight samples. The forced header also produced
  equivalent final IR in all eight samples, although it changed preprocessing
  text and should not be removed without broader source coverage.
- The four `-fno-*` flags cannot be removed as a group globally: `conv2d`
  changed when they were all removed. An individual follow-up on `conv2d`
  isolated `-fno-threadsafe-statics` as the cause; the other three were
  equivalent there. They remain conservative compatibility flags.
- The project `ap_types` include is ordered after the Vitis autopilot include,
  so Vitis' `ap_fixed.h` and `ap_int.h` shadow the project copies in these
  compilations. That ordering deserves an explicit design decision.

## Other compiler transformations

These are not equivalent-IR-preserving transformations and affect every sample
tested here.

| Ablation | 2layer lines | 3layer | conv1d | conv2d | dense latency | dense resource | exemplar | rule4ml |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Baseline with `sroa,mem2reg` and weight removal | 1,173 | 1,958 | 17,759 | 14,895 | 15,417 | 17,722 | 5,648 | 3,928 |
| Disable canonicalization passes | 3,201 | 4,798 | 35,186 | 30,507 | 38,879 | 45,072 | 14,480 | 9,615 |
| Keep generated weight initializers | 1,579 | 2,378 | 18,972 | 16,080 | 16,685 | 18,991 | 6,068 | 4,348 |

The line counts are not a quality metric, but they demonstrate that both
choices materially define the graph input. `sroa,mem2reg` is not a harmless
cleanup, and removing weight initializers is not an equivalent compilation:
it removes value-specific startup code before LLVM emission. That is defensible
for a structural hardware CDFG only if weight values and host initialization are
explicitly out of scope.

`_collapse_static_initializers()` was also ablated on the `2layer` sample. It
made no additional change after weight initializer removal, because the relevant
startup code had already disappeared. It was not independently validated across
all families.

The remaining transformations also need to be treated as representation
choices, not neutral compiler plumbing:

- `llvm.fpga.*` calls are renamed to `vitis.fpga.*` so stock LLVM 16 does not
  try to upgrade them as known intrinsics.
- Vitis debug and LLVM metadata are removed, along with debug/noalias calls.
- `-Xclang -no-opaque-pointers` selects typed-pointer IR for the downstream
  ProGraML build.
- The optimizer runs with an analysis triple and the original target triple is
  restored afterward. The current validation checks CFG adjacency and counts of
  Vitis intrinsics/pragma carriers, but does not prove data-flow equivalence.

The compiler cache is also not configuration-aware: unless `force` is set,
existing `.ll` and pragma-dump files are reused without checking the source
contents, compiler/Vitis version, argument list, or canonicalization passes.

## History

- The Vitis argument list was introduced in `f07cc5f` on 2026-07-20.
- Unsupported pragma removal was added in `44019c7` on 2026-07-24.
- Static-initializer collapsing was added in `44019c7` on 2026-07-24.
- Weight-initializer removal was added in `d0e8db8` on 2026-07-28.
- The configurable LLVM canonicalization and CFG-preservation checks were added
  in `83cafba` on 2026-08-19.

## Common LLVM optimization passes

These are candidates for future controlled experiments, not recommendations to
enable them blindly:

| Pass | Brief purpose |
|---|---|
| `sroa` | Scalar Replacement of Aggregates; splits aggregate allocas/loads/stores into scalar pieces and often exposes further optimization. |
| `mem2reg` | Promotes eligible stack allocations to SSA registers, removing many allocas and load/store pairs. |
| `instcombine` | Performs local instruction simplification and canonical peephole rewrites. |
| `simplifycfg` | Simplifies control-flow graphs by folding branches, merging blocks, and removing unreachable paths. |
| `early-cse` | Eliminates locally redundant computations and loads early in the pipeline. |
| `gvn` | Global Value Numbering; removes equivalent expressions across broader regions. |
| `adce` | Aggressive dead-code elimination, including computations whose results are unused. |
| `dce` | Removes instructions with no observable effect and no users. |
| `dse` | Dead-store elimination; removes stores overwritten before being read. |
| `constprop` | Propagates known constant values through instructions. |
| `sccp` | Sparse conditional constant propagation, combining constant propagation with path feasibility. |
| `reassociate` | Reorders associative expressions to expose constants and common subexpressions. |
| `licm` | Loop-Invariant Code Motion; moves loop-independent work outside loops when safe. |
| `loop-simplify` | Normalizes loop structure with preheaders, dedicated exits, and simpler back edges. |
| `lcssa` | Places loop-produced values used outside a loop into exit PHI nodes. |
| `indvars` | Simplifies and canonicalizes loop induction variables and related arithmetic. |
| `loop-rotate` | Rotates loops so the common path becomes the loop latch/header-friendly path. |
| `loop-unroll` | Replicates loop bodies to reduce loop-control overhead, increasing code size. |
| `inline` | Replaces calls with callee bodies when profitable, exposing interprocedural optimization. |
| `function-attrs` | Infers function properties such as read-only, no-unwind, or does-not-return. |
| `tailcallelim` | Converts eligible tail-recursive calls into loops. |
| `loop-vectorize` | Converts suitable loop iterations into SIMD/vector operations. |
| `slp-vectorizer` | Packs independent isomorphic scalar instructions into vector instructions. |

For this dataset, any additional pass should be evaluated against at least CFG,
instruction/data-flow, HLS intrinsic, pragma-carrier, and graph-node/edge
invariants—not only whether `opt` accepts the file.
