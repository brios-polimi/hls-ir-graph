# HLS project to JSON graph

This document describes the current path from one written HLS project to one
JSON graph. hls4ml projects are the primary tested input, but neither the API
nor artifact layout depends on the wa-hls4ml dataset. It is an implementation snapshot, not a
stable dataset specification. The representation will likely change as the
resource and timing surrogate model develops.

The relevant modules are:

- [`frontends/`](../src/hls_ir_graph/frontends), which emits normalized LLVM IR
  and an explicit directive artifact;
- [`programl.py`](../src/hls_ir_graph/graph/programl.py), which converts the IR
  to ProGraML JSON;
- [`pragmas.py`](../src/hls_ir_graph/graph/pragmas.py), which turns
  compiler-reported HLS pragmas into graph nodes and edges.

[`pipeline.py`](../src/hls_ir_graph/pipeline.py) provides staged and end-to-end
project APIs. Dataset iteration and registry state are external concerns.

## Current pipeline

```text
firmware/myproject.cpp
        |
        | Vitis clang preprocessing with synthesis defines and headers
        v
temporary preprocessed C++
        |
        | Vitis clang LLVM emission + -Wdump-hls-pragmas
        +--------------------------+
        |                          |
        v                          v
normalized <project>.ll     <project>.pragmas.log
        |                          |
        | llvm2graph-16            | parsed pragma semantics
        | then graph2json          |
        v                          |
base ProGraML JSON                 |
        |                          |
        +----------+---------------+
                   |
                   | match dump records to LLVM carriers or fallback anchors
                   v
          final <project>.json
```

The caller chooses every durable output path. `wa-hls4ml-ingest` currently maps
them to:

```text
data/ll/<type>/archive_<n>/<project>.ll
data/ll/<type>/archive_<n>/<project>.pragmas.log
data/graphs/<type>/archive_<n>/<project>.json
```

The source file, tool paths, target triple, timeouts, and retention settings
come from `Config`. The graph is therefore a product of both the HLS4ML project
and the configured Vitis/ProGraML toolchain.

## 1. Compiler stage

`compile_project_to_ll()` resolves the project and output paths, expects the
configured source file (currently `firmware/myproject.cpp`), and delegates to
the Vitis-specific compiler path.

### Synthesis-oriented preprocessing

The first Vitis clang invocation preprocesses the C++ with:

- the FPGA target configured by `vitis_target`;
- Vitis Autopilot and project `ap_types` include paths;
- the Autopilot SSDM header force-included;
- `__VITIS_HLS__`, `AESL_SYN`, `__SYNTHESIS__`, and `__HLS_SYN__` defined;
- HLS-oriented compiler flags such as `-fhls` and disabled C++ runtime
  facilities.

This selects the synthesis-facing branches of the generated HLS4ML source
rather than normal host-simulation branches.

One compatibility edit is applied only to the temporary preprocessed copy:
`#pragma HLS RESOURCE ... core=ROM_nP_BRAM` is removed. Current Vitis rejects
this legacy core name in generated Conv1D projects, while the directive is not
needed to construct the compiler CDFG. The extracted source project itself is
not modified.

### LLVM emission and pragma capture

The second Vitis clang invocation emits textual LLVM IR with typed pointers
(`-no-opaque-pointers`). At the same time, `-Wdump-hls-pragmas` asks Vitis to
report recognized HLS pragmas. Compiler standard error is saved as the
project's `.pragmas.log`; it can contain other diagnostics as well as pragma
records.

The raw LLVM is normalized before it becomes a durable artifact:

1. `llvm.fpga.*` names are changed to `vitis.fpga.*`. This prevents stock LLVM
   16 tooling from treating incompatible Vitis intrinsics as LLVM intrinsics;
   they remain external calls and retain graph connectivity.
2. Bodies of `__cxx_global_var_init*` functions are replaced with a return.
   HLS4ML weight and bias arrays remain as typed globals, but large host-startup
   `ap_fixed(double)` construction sequences are excluded from the hardware
   graph.
3. debug, no-alias, and other LLVM metadata not used by this CDFG flow is
   removed.

On success, the registry records `COMPILED` and the LLVM path. If both the
`.ll` and `.pragmas.log` already exist, compilation is reused unless forced.

## 2. ProGraML graph stage

`graph_project()` requires an existing registry project record and invokes two
external binaries:

```text
llvm2graph-16 <project.ll> | graph2json > project.json
```

The implementation buffers the first process's protobuf output in memory and
passes it to the second process. The resulting JSON initially follows the
ProGraML program-graph schema: LLVM instructions, variables/constants, and
their control, data, and call relations.

This base graph is not yet the final artifact. `grapher.py` requires the
matching `.pragmas.log` and calls `inject_vitis_pragmas()` in place. Pragma
injection is therefore a mandatory part of successful graphing, not an
optional postprocessing step. The registry becomes `GRAPHED` only after both
conversion and injection succeed.

## 3. Pragma parsing and injection

Vitis exposes useful pragma information in two incomplete but complementary
forms:

- the diagnostic dump contains source-level directive names, functions,
  options, and locations;
- LLVM contains some variable-related directives as `llvm.sideeffect` calls
  with `xlx_*` operand bundles, which provide precise CDFG anchors.

The dump is treated as authoritative for semantics. LLVM carriers are treated
primarily as attachment evidence.

### Reading semantic records

`read_vitis_pragma_dump()` recognizes Vitis's
`HLS pragma dump ... [-Wdump-hls-pragmas]` diagnostics. Directive and argument
keys are normalized to lowercase identifiers. Options are tokenized into
multi-valued `key=value` arguments, bare flags, or stable positional fields.
Values of `variable` and `port` arguments become candidate target names.
Unknown directives and arguments are retained rather than discarded.

### Finding LLVM carriers

Instruction nodes are scanned for `llvm.sideeffect` calls containing an
`xlx_<directive>` bundle. SSA names in the bundle become candidate targets.
Variable nodes with data-flow edges into the carrier instruction are also
collected as possible anchors. Scalar constants in carrier operands are kept
as generic positional arguments.

### Matching and scope anchoring

LLVM function symbols are demangled and matched by exact source-level function
identifier. The source function signature additionally distinguishes stream
and non-stream overloads with the same name. Carrier matching requires a
compatible directive, exact target identity, and function or global-owner
evidence. `STREAM` and `reqd_pipe_depth` are the one explicit directive alias.

Without a carrier, a variable pragma attaches to all graph occurrences of the
exact local or LLVM-global object. For a target-free pragma inside a labelled
source loop, the injector matches that label to the same-named basic block in
each exact LLVM function specialization. It adds explicit basic-block nodes,
block CFG edges, and bidirectional block/instruction membership, then attaches
the pragma to the matched block. Target-free directives without exact
loop-label evidence attach to the function's LLVM entry block as a clearly
marked coarse scope.
Unresolved target-bearing records do not fall back to an unrelated entry. See
[`LOOP_PRAGMA_SCOPE_DIAGNOSIS.md`](LOOP_PRAGMA_SCOPE_DIAGNOSIS.md) for the
source-to-LLVM-to-graph trace and motivation.

A dump record with no usable function or target anchor is not made into a
model node. It is preserved in
`pragma_injection.unmatched_records`. Conversely, an LLVM carrier with no dump
record is injected as a `carrier_only` pragma so that compiler
evidence is not lost.

Before injection, nodes created by current or older pragma injectors are
removed and remaining node IDs and links are renumbered. This makes reinjection
idempotent for an otherwise unchanged base graph.

### Resolving template-dependent numeric arguments

The literal dump deliberately preserves expressions such as
`CONFIG_T::reuse_factor`, `block_factor`, and `ii`. For each concrete graph
function, the injector obtains its template arguments from the demangled LLVM
symbol and the corresponding template parameter names/local constant
declarations from the reported source function.

All symbolic numeric arguments for one project are evaluated in one temporary
Vitis Clang probe translation unit. This delegates C++ templates, generated
`parameters.h` constants, macros such as `DIV_ROUNDUP`, aliases such as
`CONFIG_T::mult_config`, and local `const`/`constexpr` expressions to the same
frontend used for LLVM production. The original expression stays verbatim in
`pragma_text` and `raw_options`; `arguments_json` receives the concrete number.
Resolution details are stored in `numeric_resolution_json`.

This runs during graph production because the concrete function
instantiations are known only after LLVM-to-graph conversion. It reuses the
retained LLVM and pragma dump but requires the extracted source project. It
does not recompile or replace the retained project LLVM.

### Injected graph contract

Each injected pragma is:

- a node with `type: 3` and `text: "pragma.<directive>"`;
- connected from the pragma node to its anchors using `flow: 3`;
- annotated with normalized arguments, raw options, source provenance,
  carrier operands, and the reason for its chosen anchor.

Each mapped LLVM basic block is a `type: 4` node with its exact name in
`features.name`. `flow: 4` represents block CFG and block/instruction
membership. The retained LLVM is parsed once, and a function is enriched only
when its LLVM block count exactly matches the ProGraML block order.

Top-level `pragma_injection` metadata records schema version 2, the number of
dump records, and all unmatched records. The complete field contract is in
[`PRAGMA_SCHEMA.md`](PRAGMA_SCHEMA.md).

## Reuse and regeneration behavior

The current cache checks are artifact- and registry-based:

- during a normal archive run, `processor.py` reuses a canonical `.ll` file if
  it exists; `compile_project_to_ll()` itself requires both the `.ll` and
  `.pragmas.log` for its early-return path;
- `processor.py` reuses a graph found through the registry or at the canonical
  graph path, while `graph_project()` has an additional fast path for a
  `GRAPHED` or `READY` record whose graph exists;
- `--force-recompile` starts from source and rebuilds downstream artifacts;
- `--force-regraph` starts from retained `.ll` files and requires matching
  `.pragmas.log` files.

These checks do not compare source hashes, configuration, tool versions, or
schema versions. A toolchain or normalization change therefore requires an
intentional forced rebuild and a separately versioned dataset/manifest. A
reused `.ll` without its matching pragma dump cannot produce a new graph.

## What this representation captures

The resulting JSON is best understood as a compiler-derived, pragma-augmented
LLVM program graph. It captures:

- lowered operations and their data/control connectivity;
- typed variables and constants represented by ProGraML;
- functions and calls surviving LLVM emission;
- recognized HLS directives and their best available attachment points.

It does **not** directly capture the HLS scheduler's decisions, operation
binding, memory banking and port realization, achieved initiation intervals,
RTL structure, placement, routing, or target-device timing paths. Resource and
timing labels are learned from correlations between the compiler graph,
pragmas, and those later implementation outcomes.

## Limitations and modeling risks

### The graph is upstream of the prediction target

Resource use and timing are strongly affected by scheduling, binding, memory
inference, clock constraints, device family, and backend heuristics. Most of
that state is absent. Two designs with similar LLVM CDFGs can synthesize
differently, and a tool upgrade can change results without a corresponding
source-level graph change.

### Normalization deliberately changes the compiler output

Removing one legacy resource pragma, collapsing static initializers, renaming
intrinsics, and stripping metadata make the graph tractable and compatible,
but move it away from the exact input seen by the full synthesis flow. In
particular, removing initialization operations avoids a large non-hardware
artifact but may also make constant values less explicit to downstream graph
features.

### Pragma attachment is heuristic

Source function names may not match mangled or instantiated LLVM functions.
Target names can be transformed, duplicated, or optimized away. A
function-entry fallback preserves the directive but gives only coarse scope,
while same-directive carrier matching can be ambiguous when several similar
pragmas exist. Regex parsing also depends on Vitis diagnostic and IR formatting.
`unmatched_records` and `anchor_reason` should therefore be monitored as data
quality signals, not ignored.

### Generic LLVM graphs underrepresent HLS semantics

ProGraML understands program structure, but Vitis-specific calls, fixed-point
operations, streams, interfaces, arrays, and memory behavior may appear only as
generic calls or low-level operations. Important hierarchical HLS concepts can
be fragmented across many nodes or omitted entirely.

### The representation is Vitis-specific

Compilation flags, pragma diagnostics, `xlx_*` carriers, and compatibility
rewrites are tied to Vitis. A Bambu graph generated through a different
frontend is not automatically feature-equivalent even if both outputs use
LLVM. Comparing backends requires explicit provenance and either a shared
semantic schema or backend-aware model inputs.

### Artifact reuse can silently retain stale graphs

Existence-based caching does not detect changes to source, compiler flags,
Vitis, ProGraML, or injection logic. The temporary preprocessed source and raw
LLVM are also discarded, limiting later diagnosis unless the durable LLVM and
diagnostic dump are sufficient.

### Dataset and feature choices can hide useful information

The JSON preserves more pragma information than the downstream tensorizer
necessarily uses. A closed learned vocabulary, unknown-token mapping, graph
size truncation, pooling strategy, or train/test split can erase distinctions
that exist in the producer artifact. HLS4ML projects generated from closely
related configurations also require grouping-aware splits to avoid measuring
memorization instead of generalization.

## Possible directions

These approaches are not mutually exclusive. A practical next representation
may combine several views rather than replace the current graph outright.

### Augment the current graph with synthesis evidence

Parse Vitis and Bambu reports for loop schedules, achieved II, latency,
operation binding, inferred memories, interface details, and clock constraints.
Attach only information available at the intended prediction point: using
post-synthesis facts as inputs would leak the target for a pre-synthesis
surrogate, but intermediate estimates may support a deliberately staged model.

This is the smallest change with the clearest path from the current pipeline.

### Build a common HLS semantic layer

Introduce backend adapters that map Vitis pragmas and Bambu options into common
concepts such as pipelining, unrolling, array partitioning, interfaces, memory
implementation, and allocation limits. Preserve backend-specific raw fields
alongside the common fields. This makes cross-backend experiments possible
without pretending that the toolchains are identical.

### Use a multi-view model

Keep the LLVM CDFG, but supply project-level inputs separately: target device,
clock period, backend/version, HLS4ML configuration, precision, reuse factors,
strategy, array shapes, and other global constraints. A small tabular or
hierarchical encoder can be fused with the graph embedding. This avoids forcing
global configuration into arbitrary graph nodes.

### Add a higher-level HLS4ML or source graph

Construct a graph from the HLS4ML model/configuration or a Clang AST before
lowering. Layers, loops, arrays, types, shapes, and source-level pragma scope
are easier to represent there. Linking high-level entities to LLVM nodes would
retain both semantic clarity and low-level operation detail.

### Use an MLIR/CIRCT-style intermediate representation

An IR with explicit loops, affine bounds, memories, streams, and hardware
types may be a better learning representation than generic LLVM. This can
reduce the need to reconstruct HLS concepts from calls and text, at the cost of
building or maintaining a new extraction path and mappings for both backends.

### Learn from scheduled IR or RTL/netlists

Graphs built after scheduling or from RTL/netlists are closer to actual
resource and timing outcomes. They can support more accurate post-HLS
prediction and attribution, but are more expensive to generate, much larger,
more tool-specific, and unsuitable when the goal is prediction before running
HLS. They are also valuable as teacher representations for distillation into a
cheaper pre-HLS model.

## Evolution guidelines

When changing this pipeline:

1. version the graph schema and record Vitis, ProGraML, backend, target, and
   pipeline revision with the dataset;
2. keep semantic provenance separate from inferred attachment so uncertainty
   remains measurable;
3. quantify unmatched pragmas, fallback anchors, graph sizes, and failures by
   kernel family;
4. rebuild through explicit manifests rather than mixing graph generations;
5. evaluate representation changes with grouped splits and per-kernel-family
   errors, not only aggregate metrics.

These practices allow the representation to evolve without making experiments
from different graph generations appear directly comparable.
