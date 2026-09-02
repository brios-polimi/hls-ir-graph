# Extension points

Add a backend by implementing the `Frontend.compile` contract and registering
it in `frontends.FRONTENDS`. Its result must state directive semantics, frontend
fidelity, compiler identity, invoked commands, and known gaps.

Post-frontend LLVM processing uses `hls_ir_graph.transforms.register`. Configure
an ordered list in `PreprocessConfig.ir_transforms`; every transform is applied
after backend LLVM emission and its name/options are saved in provenance. This
is the appropriate seam for compiler-level augmentation or backend-specific
normalization that should be independently ablated.

The built-in `llvm_opt` transform runs a configured LLVM pass pipeline under an
analysis triple, restores the frontend target triple, and rejects changes to CFG
adjacency or Vitis intrinsic/pragma-carrier counts.

Graph enrichment belongs under `graph/` and should consume explicit artifacts,
not infer a dataset layout. Learning-time graph/tensor augmentation belongs in
`hls-surrogate-lab`, where it can be seeded and applied per sample without
rewriting canonical LLVM or graph files.
