# Graph construction

This subtree owns ProGraML conversion and versioned graph enrichment. Preserve
node/edge IDs, source/compiler evidence, and requested-versus-realized directive
semantics. Schema changes require focused synthetic tests and corresponding
updates to `docs/ARTIFACT_CONTRACT.md` or `docs/PRAGMA_SCHEMA.md`.

Dataset labels may be attached through the public API but dataset selection,
registries, tensorization, and learning features do not belong here.
