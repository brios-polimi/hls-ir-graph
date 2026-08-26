# Loop pragma scope diagnosis

## Conclusion

Before attachment schema 3, target-free pragmas were matched to the correct
concrete LLVM function, but loop pragmas were not attached to the loop they
controlled. The `function_scope_entry` fallback only stated that the pragma
occurred somewhere in that function; it did not preserve loop identity or
scope.

This is a material representation defect for resource and timing prediction.
The model can learn that a function contains, for example, a pipelined loop,
but it cannot distinguish which of several loops is pipelined. The present
edge does not make the pragma semantically reach the intended loop through
message passing.

The defect described below was the pre-change behavior. Attachment schema 3
now implements the labelled-loop repair described later in this document:
explicit named block nodes are created from one retained-LLVM pass, and the
pragma attaches to the same-named block.

## Concrete trace

The example is project `dense_120_96_88_10b_rf4093` from 2layer archive 1:

```text
source:
data/cache/extracted/2layer/archive_1/projects/
  dense_120_96_88_10b_rf4093/firmware/nnet_utils/nnet_dense_resource.h

LLVM:
data/ll/2layer/archive_1/0162624c-ca32-4e05-a8be-48c9d355ef4b.ll

graph:
data/graphs/2layer/archive_1/0162624c-ca32-4e05-a8be-48c9d355ef4b.json
```

The Vitis dump reports:

```text
nnet_dense_resource.h:125
PragmaType=pipeline
PragmaFunction=dense_resource_rf_gt_nin_rem0
PragmaOptions=II=1 rewind
```

The source location is unambiguous:

```cpp
ReuseLoop:
for (int ir = 0; ir < CONFIG_T::reuse_factor; ir++) {
    #pragma HLS PIPELINE II=1 rewind
    // ...

MultLoop:
    for (int im = 0; im < block_factor; im++) {
        #pragma HLS UNROLL
        // ...
    }
}
```

Exact demangling correctly maps the reported source function to two concrete
template specializations in this graph. For the `config2` specialization, the
pipeline pragma is node `6770`, function `12`. Its attachment metadata says:

```text
anchor_reason=function_scope_entry
attachment_confidence=coarse_scope
```

Its only `applies_to` target is instruction node `1423`, in graph block `29`:

```llvm
%this.addr.i377 = alloca %"struct.ap_fixed_base<17, 7>"*, align 8
```

That is simply the first instruction in the function. It has no special
relationship to `ReuseLoop`.

The retained LLVM does preserve the useful source loop label:

```llvm
for.end14:
  br label %ReuseLoop

ReuseLoop:
  ; initialize the loop induction variable
  br label %for.cond16

for.cond16:
  ; loop condition
  br i1 %cmp17, label %for.body19, label %for.cond.cleanup18

for.body19:
  ; outer-loop body
  br label %MultLoop

MultLoop:
  ; initialize the nested-loop induction variable
  br label %for.cond22
```

In the JSON graph, the relevant block mapping is:

| Source/LLVM role | LLVM block | Graph block | First instruction node |
| --- | --- | ---: | ---: |
| Current fallback | function entry | 29 | 1423 |
| Labeled outer-loop preheader | `ReuseLoop` | 51 | 2073 |
| Outer-loop condition/header | `for.cond16` | 52 | 2080 |
| Outer-loop body | `for.body19` | 54 | 2093 |
| Labeled nested-loop preheader | `MultLoop` | 55 | 2106 |
| Outer-loop latch | `for.inc45` | 103 | 3926 |

Following only directed instruction `control` edges, the shortest path from
the current entry target to the first instruction in:

- the `ReuseLoop` preheader is 249 edges;
- the actual condition/header is 253 edges;
- the outer-loop body is 256 edges;
- the nested `MultLoop` preheader is 262 edges.

The pragma-to-entry `applies_to` edge adds one more message-passing step.

## Why the current model cannot recover the scope

`hls-surrogate-lab` tensorizes the attachment as:

```text
pragma --applies_to--> instruction
```

The heterogeneous GNN passes messages in that direction. Its default is three
layers, so a pragma attached at function entry can influence at most the entry
and the next two directed graph hops during local message passing. In this
example, reaching the loop header would require 254 layers along the shortest
relevant path.

The graph-level pooling still exposes the pragma node itself to the regression
head. The model can therefore learn global correlations such as “a
`PIPELINE II=1` pragma exists.” It cannot reliably learn:

- that the pragma controls `ReuseLoop` rather than `InitAccum`, `IndexLoop`,
  `MultLoop`, or `Result`;
- the operations and trip count inside the controlled loop;
- that the `UNROLL` at line 131 belongs to the nested `MultLoop`;
- interactions between a pipelined outer loop and an unrolled inner loop.

Increasing GNN depth is not an appropriate correction. Hundreds of
instruction-level layers would be impractical and would introduce severe
over-smoothing. It would also leave the actual loop identity implicit.

## How this happens

The current evidence sources each solve a different part of the problem:

1. The Vitis dump provides the source path, line, directive, options, and
   source function name.
2. Exact demangling maps that source function to its concrete LLVM template
   specializations.
3. `llvm.sideeffect` carriers provide exact object anchors for only a subset
   of variable directives. `PIPELINE` and `UNROLL` have no equivalent carrier
   here.
4. For a target-free record without a carrier, `pragmas.py` deliberately uses
   the first instruction of every exact function match.

The fallback is conservative about *which function* receives the record, but
it discards *where inside the function* the directive applies.

There is currently no source-line-to-LLVM-location mapping in the graph. The
compiler compatibility pass also removes debug and loop metadata, and the
ProGraML JSON retains numeric block IDs but not LLVM basic-block names.
Nevertheless, generated hls4ml code and the retained textual LLVM both preserve
labels such as `ReuseLoop` and `MultLoop`, which are strong mapping evidence
that the current injector does not use.

## Pre-schema-3 scope in the rebuilt archive-1 graphs

The following is a read-only scan of the current rebuilt graphs. “Loop-scoped”
means that a small lexical brace-stack check found the source pragma inside a
`for`, `while`, or `do` body. It is an audit estimate rather than a C++ parser,
but it classifies the shown generated hls4ml patterns directly.

| Kernel family | All `function_scope_entry` nodes | Loop-scoped nodes | Unique loop pragma locations |
| --- | ---: | ---: | ---: |
| 2layer (100 graphs) | 1,100 | 800 | 400 |
| conv1d (49 graphs) | 2,748 | 1,404 | 489 |
| exemplar (100 graphs) | 2,330 | 1,156 | 468 |
| **Total** | **6,178** | **3,360** | **1,357** |

Loop-scoped nodes by directive:

| Kernel family | `pipeline` | `unroll` |
| --- | ---: | ---: |
| 2layer | 200 | 600 |
| conv1d | 609 | 795 |
| exemplar | 235 | 921 |

Thus this is not an isolated bad anchor. At least 3,360 current model nodes
represent loop directives using a function-entry edge.

The remaining coarse-scope nodes include genuinely function-scoped directives
such as `INLINE` and `DATAFLOW`, as well as some source `PIPELINE`, `UNROLL`,
and `ALLOCATION` placements whose semantics need separate classification.
They should not all be treated as loops merely because they lack a target.

## Implemented repair

Attachment schema 3 separates exact function identity from exact
within-function scope:

1. Read the recorded source file and line for target-free directives.
2. For a pragma lexically inside a generated hls4ml loop, recover the label
   attached to that enclosing loop, such as `ReuseLoop`.
3. Parse basic-block names and successors from the retained textual LLVM for
   each exact concrete function specialization.
4. Map the source label to the same-named LLVM block (`ReuseLoop` in this
   example), retaining its CFG successor to the natural header
   (`for.cond16`).
5. Attach the pragma to that named block and record the source label, LLVM
   block name, graph block ID, and exact `source_loop_label` reason. If the
   function's LLVM and ProGraML block counts disagree or the label is missing,
   preserve the pragma as unresolved scope rather than presenting a
   function-entry anchor as localized.

This corrects attachment without recompiling: the current source, `.ll`,
`.pragmas.log`, and base graph contain the required evidence. The graph must be
reinjected or regenerated.

Block nodes have CFG edges and bidirectional membership edges to their
instructions. A loop pragma can therefore affect the labelled block in one
message-passing layer, its member instructions and successor header block in
the next, instead of traversing hundreds of instruction edges. A future
explicit natural-loop node remains a possible ablation if block-level
structure is insufficient.

Debug-location preservation is a more general alternative for arbitrary C++,
but it requires compiler-pipeline and graph-schema changes and Vitis/LLVM
compatibility validation. For the generated hls4ml corpus, existing named loop
labels are the simplest strong signal to test first.

## Acceptance tests

A corrected attachment pass should establish all of the following on the
concrete example:

1. The line-125 `PIPELINE II=1 rewind` maps to both concrete specializations of
   `dense_resource_rf_gt_nin_rem0`, but within each one it maps specifically to
   the `ReuseLoop` natural loop.
2. The line-131 `UNROLL` maps to nested `MultLoop`, not the outer loop or
   function entry.
3. The unroll pragmas for `InitAccum`, `IndexLoop`, and `Result` map to their
   own loops and remain distinguishable.
4. A source label must map to exactly one block per concrete function; missing
   or duplicate matches are reported, not guessed.
5. The stored attachment includes independently readable source and LLVM
   provenance so the notebook can display why the loop match was accepted.
6. An audit reports zero `function_scope_entry` attachments for source pragmas
   proven to be lexically loop-scoped.

Only after these pass is it meaningful to compare header-only attachment
against explicit loop/block nodes in a model ablation.
