# LLVM debug type recovery

The Bambu frontend needs no patched compiler, native plugin, ELF object, or
DWARF-reading dependency. `-g -fstandalone-debug` puts source type information
directly in textual LLVM 16 metadata. A pure-Python extractor builds a table of
debug record types and their proven associations with LLVM identified types.

## Evidence and coverage

`llvm.dbg.declare`, `llvm.dbg.value`, and `DIGlobalVariableExpression` connect
LLVM operands/globals to source variables and their types. The extractor follows
typedefs, qualifiers, pointer/reference types, array element types, and namespace
scopes. It does not use LLVM type suffix numbers, matching declaration order,
or similar layouts to infer template arguments.

Clang sometimes emits initialized constants as anonymous wrappers around their
storage class. An anchored source type can be followed down a unique, same-size,
zero-offset member/inheritance chain to identify that storage class. Multiple
members, virtual bases, offsets, or size changes reject this inference.

Only unambiguous associations rename LLVM record identifiers; layout, operands,
and operations are unchanged by name recovery. Conflicting names stay unresolved.
Token-aware rewriting protects quoted strings/comments and distinguishes names
such as `%class.ac_fixed` and `%class.ac_fixed.10`. Duplicate source names retain
distinct LLVM identities using a `.debug.N` suffix.

`<top>.types.json` contains the original and emitted names, source spellings,
debug IDs, evidence, unmapped/conflicting types, and the global debug-record
table. IDs refer to the retained `<top>.debug.ll`; its hash is recorded. The
table describes the frontend snapshot, before optional configured transforms.

**Complete AC coverage means every emitted identified AC record type has an
unambiguous mapping.** It does not mean every C++ temporary survives lowering,
every `iN` has a source fixed-point type, or every aggregate is a scalar numeric
value. Debug-only records may have no LLVM counterpart. Nonempty debug
expressions/fragments are not treated as whole-object type evidence. Unsupported
cases fail the default coverage check rather than receiving guessed names.

Source template spelling remains evidence of the declared type, not a claim
about the backend's realized hardware or a memory layout's effective bit width.

## Compiler policy

Bambu uses `-O0 -Xclang -disable-llvm-passes`: `-O0` alone still runs mandatory
passes such as always-inlining. Type extraction must happen before those can
erase associations. Any desired optimization belongs in the separately recorded
IR transform stage after names have been recovered. `-Xclang -disable-O0-optnone`
prevents implicit `optnone` attributes from blocking those later transforms; it
does not enable optimization during extraction.

The remaining flags have explicit roles:

| Flags | Role |
| --- | --- |
| `-std=c++14`, `-ftemplate-depth=2048` | AC library language/template requirements |
| `__BAMBU__`, `__SYNTHESIS__`, project AC include | Select the written project's synthesis source |
| `-m64` | Fixed frontend ABI, independent of host default |
| `-fno-exceptions`, `-fno-threadsafe-statics` | HLS language/runtime policy |
| `-fwrapv`, `-ffp-contract=off` | Preserve the previous explicit arithmetic policy |
| `-g -fstandalone-debug` | Complete available source record/template descriptions |
| `-Xclang -no-opaque-pointers` | LLVM-16 ProGraML's typed-pointer contract |
| `-Wno-unknown-pragmas` | Requests are captured separately, not interpreted by portable Clang |

The previous `-O2`, explicit `__NO_INLINE__` (already defined at `-O0`), forced
inlining, vectorization/unrolling controls, builtin-memory suppression, stack
protector, and `__cxa_atexit` flag overrides have been removed. Preprocessing and
emission use the same language/arithmetic policy; include paths/macros are only
needed in preprocessing. Template-depth applies only to compilation.

This changes the graph representation intentionally. It does not make portable
LLVM Bambu-internal IR. Existing weight-initializer removal/static-initializer
collapse policies have not been changed by this work.

## Bounded validation (2026-08-31)

One recovered model from each user-selected archive was used; no archive-wide
compilation, dataset modification, or synthesis was performed.

| Model family / source ID | Mapped AC records | Graph nodes | Graph edges |
| --- | ---: | ---: | ---: |
| 2layer / `0162624c-ca32-4e05-a8be-48c9d355ef4b` | 12/12 | 2,632 | 5,482 |
| conv2d / `00dc820c-b275-4dcd-b439-64e13df52c55` | 137/137 | 35,495 | 74,619 |

Both modules pass `llvm-as-16`, ProGraML extraction, and inference-mode tensor
construction. The tensorizer now recognizes AC integer/storage templates,
channel/fifo payloads, and debug unsigned array dimensions (`32U`), using schema
version 3 without changing vector dimensions.

The 2layer graph has no unknown type embeddings. Conv2d retains generic embeddings
for five custom `exponent_scale*_t` record families, function-pointer types, and
six anonymous-aggregate nodes containing an AC pointer plus an integer. The AC
field's full name is present, but the aggregate is not falsely labeled as one
fixed-point scalar. All mapped identified AC record spellings are recognized.
Including AC-containing stream/array wrappers, conv2d has 165 recognized mapped
record spellings; the 137 count above counts the AC library records themselves.

Validation artifacts are under `/tmp/hls-debug-types-{2layer,conv2d}/graph-artifacts`;
the combined tensorization summary is `/tmp/hls-debug-types-validation.json`.
These are local temporary artifacts, not versioned fixtures or universal coverage
claims. Synthetic tests cover conflicts, missing metadata, fragments, transparent
constant storage, duplicate spellings, token boundaries, and real Clang emission.
