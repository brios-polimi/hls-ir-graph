# Structured pragma schema

`hls-ir-graph` injects Vitis pragmas as nodes with `type: 3` and
`text: "pragma.<directive>"`. Schema version 2 makes the compiler dump the
authoritative semantic source and uses `llvm.sideeffect` carriers only for
precise graph attachment.

Each injected pragma node has these single-value feature lists:

- `schema_version`: currently `"2"`;
- `directive`: normalized lowercase directive name;
- `full_text`: the complete matching `.pragmas.log` diagnostic line verbatim,
  excluding only its line terminator;
- `pragma_text`: the following literal `#pragma HLS ...` line copied verbatim
  from `.pragmas.log`, for example
  `#pragma HLS PIPELINE II=CONFIG_T::reuse_factor`;
- `raw_options`: lossless `PragmaOptions` text from Vitis;
- `arguments_json`: JSON object mapping normalized keys to lists of values;
- `numeric_resolution_json`: present when a symbolic numeric expression was
  compiler-evaluated; records its original expression, resolved value, and
  concrete template arguments;
- `carrier_arguments`: raw LLVM carrier operands, when present;
- `target_names`, `reported_function`, `source_file`, and `source_line`:
  provenance;
- `attachment_schema_version`: currently `"3"`;
- `attachment_confidence`: `exact`, `coarse_scope`, or `carrier_only`;
- `resolved_global_symbols`: exact LLVM global symbols found in a carrier;
- `anchor_reason`: `carrier_exact`, `variable_identity`,
  `source_loop_label`, `function_scope_entry_block`, `function_scope_entry`,
  or `carrier_only`;
- `source_loop_label`: the exact source/LLVM loop label for a
  `source_loop_label` attachment, otherwise empty.

Target names are represented semantically by `FLOW_PRAGMA` edges and are not
included in learned node features. A local object attaches to every variable
occurrence with its exact `%name` or conventional `%name.*` alias inside the
exact demangled function. A carrier using an LLVM global attaches to every
constant occurrence containing that exact `@symbol`. It does not attach to
instructions merely because their text mentions the name. Scalar carrier
constants are retained as `_carrier_arg_<position>` arguments because their
directive-specific meaning is not stable enough to guess in the producer.

Dump and carrier records match only with compatible directives, an exact
target, and exact function/global-owner evidence. Source `STREAM` and LLVM
`reqd_pipe_depth` are one explicit compatibility pair; their depths must agree
when both are present. A target-bearing record that cannot resolve remains
unmatched. A target-free pragma lexically enclosed by a labelled source loop
attaches to the same-named LLVM basic-block node in every exact concrete
function specialization. Other target-free directives use the function's LLVM
entry block as a clearly labelled coarse scope anchor. The first-instruction
fallback remains only for injection without block enrichment.

Attachment schema 3 also introduces LLVM basic-block nodes:

- `type: 4`, with stable text `llvm.basic_block`;
- exact LLVM block name in `features.name`;
- `features.is_source_loop` indicating that the name was recovered as an
  enclosing source loop label;
- bidirectional block/instruction membership and block CFG edges using
  `flow: 4`.

The tensor schema represents these as `block` nodes and the relations
`block -> instruction`, `instruction -> block`, and `block -> block`.
Loop pragmas use `pragma -> block`. Exact names remain inspectable JSON
provenance; learned block features use only stable entry/named/source-loop
flags.

Compiler dump records that cannot be tied to an instantiated graph function
are kept under top-level `pragma_injection.unmatched_records`. They are not
injected as model nodes because included template headers can contain valid
pragmas for functions that are absent from the synthesized CDFG.

For injected nodes, numeric arguments in `arguments_json` are always finite
numeric strings. Symbolic numeric expressions are evaluated by a temporary
Vitis Clang probe using the concrete demangled template arguments and source
constant declarations. Graph production fails rather than silently retaining
an unresolved numeric model input. In `ALLOCATION operation instances=mul`,
`instances` names the allocated operation and is therefore not a numeric
argument; `limit` is numeric.

`hls-surrogate-lab` uses a deliberately closed feature schema. Selected
resource/timing-relevant directives receive named numeric value/mask slots and
named categorical flags. Other known directives receive only their directive
ID; unknown directives map to `UNK`. Unlisted arguments are not tensorized.
They remain losslessly available in `arguments_json`, `raw_options`, and the
durable `.pragmas.log` so the explicit schema can be extended and graphs
regenerated without recompiling LLVM.

Because ProGraML represents LLVM globals as constants, the tensor schema
includes `("pragma", "applies_to", "constant")`. Existing graphs and tensors
must be rebuilt for attachment schema version 3 and its block nodes.
