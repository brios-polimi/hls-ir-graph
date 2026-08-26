# Compiler frontends

Each backend adapter owns headers, synthesis defines, compiler invocation, and
the fidelity statement for its emitted LLVM. Common textual cleanup lives in
`common.py`; backend-only compatibility edits stay with their adapter.

Do not report portable Clang output as Bambu-internal IR. Preserve the explicit
fidelity gap until a Bambu compiler-reported artifact is proven usable. New
post-frontend experimentation should use the configured `transforms/` hook and
must record its stable name/options in provenance.
