"""Backend-neutral staged project preprocessing API."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from .config import PreprocessConfig
from .frontends import FRONTENDS
from .frontends.base import CompileResult
from .frontends.bambu import SourceDirective
from .graph.hierarchy import add_llvm_hierarchy
from .graph.intrinsics import prune_nonsemantic_intrinsics
from .graph.programl import graph_via_binary
from .graph.relations import canonicalize_relations
from .transforms import IrTransformContext, apply_transforms


@dataclass(frozen=True)
class ProjectArtifacts:
    llvm: Path
    directives: Path
    compiler_log: Path
    graph: Path | None
    provenance: Path
    type_table: Path | None = None
    debug_llvm: Path | None = None


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def _read_json(path: Path) -> dict:
    with path.open() as handle:
        return json.load(handle)


def _read_source_directives(path: Path) -> list[SourceDirective]:
    return [SourceDirective(**json.loads(line)) for line in path.read_text().splitlines() if line.strip()]


def compile_project(
    project_dir: str | Path,
    llvm_path: str | Path,
    directives_path: str | Path,
    compiler_log: str | Path,
    *,
    backend: str,
    source_file: str | None = None,
    config: PreprocessConfig | None = None,
) -> CompileResult:
    """Compile a written HLS project into explicit caller-owned paths."""

    backend = backend.lower()
    if backend not in FRONTENDS:
        raise ValueError(f"Unsupported backend: {backend}")
    cfg = config or PreprocessConfig()
    project = Path(project_dir).resolve()
    source = (project / (source_file or cfg.source_file)).resolve()
    if not source.is_file():
        raise FileNotFoundError(f"HLS source not found: {source}")
    outputs = tuple(Path(path).resolve() for path in (llvm_path, directives_path, compiler_log))
    for output in outputs:
        output.parent.mkdir(parents=True, exist_ok=True)
    result = FRONTENDS[backend]().compile(project, source, *outputs, cfg)
    applied = apply_transforms(
        outputs[0],
        cfg.ir_transforms,
        IrTransformContext(
            backend=backend,
            project_dir=project,
            source_file=source,
            directives_file=outputs[1],
        ),
    )
    return replace(result, applied_transforms=tuple(applied))


def _enrich_bambu_graph(
    graph_path: Path,
    llvm_path: Path,
    directives_path: Path,
    provenance: dict,
) -> None:
    graph = _read_json(graph_path)
    nodes = graph.setdefault("nodes", [])
    links = graph.setdefault("links", [])
    _, _, hierarchy = add_llvm_hierarchy(graph, nodes, links, llvm_path, set())
    graph["hierarchy_enrichment"] = hierarchy
    graph["intrinsic_pruning"] = prune_nonsemantic_intrinsics(graph)
    graph["source_directives"] = {
        "schema_version": 1,
        "semantics": "requested_not_realized",
        "records": [asdict(item) for item in _read_source_directives(directives_path)],
    }
    graph["preprocessing"] = provenance
    graph["relation_enrichment"] = canonicalize_relations(graph)
    graph_path.write_text(json.dumps(graph, separators=(",", ":")))


def graph_llvm(
    llvm_path: str | Path,
    graph_path: str | Path,
    directives_path: str | Path,
    *,
    backend: str,
    project_dir: str | Path,
    config: PreprocessConfig | None = None,
    label: dict | None = None,
    provenance: dict | None = None,
) -> Path:
    """Convert LLVM to JSON and attach backend-specific graph semantics."""

    backend = backend.lower()
    if backend not in FRONTENDS:
        raise ValueError(f"Unsupported backend: {backend}")
    cfg = config or PreprocessConfig()
    llvm = Path(llvm_path).resolve()
    graph = Path(graph_path).resolve()
    directives = Path(directives_path).resolve()
    graph.parent.mkdir(parents=True, exist_ok=True)
    graph_via_binary(llvm, graph, cfg.programl)
    if backend == "vitis":
        from .graph.pragmas import inject_vitis_pragmas

        inject_vitis_pragmas(
            graph, directives, llvm_path=llvm,
            project_dir=Path(project_dir).resolve(), cfg=cfg, label=label,
        )
    else:
        _enrich_bambu_graph(graph, llvm, directives, provenance or {})
    return graph


def preprocess_project(
    project_dir: str | Path,
    output_dir: str | Path,
    *,
    backend: str,
    source_file: str | None = None,
    config: PreprocessConfig | None = None,
    graph: bool = True,
) -> ProjectArtifacts:
    """Compile and optionally graph one already-written HLS project."""

    cfg = config or PreprocessConfig()
    project = Path(project_dir).resolve()
    source = (project / (source_file or cfg.source_file)).resolve()
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    stem = source.stem
    llvm = output / f"{stem}.ll"
    directives = output / f"{stem}.pragmas.log"
    compiler_log = output / f"{stem}.compiler.log"
    provenance_path = output / f"{stem}.provenance.json"
    graph_path = output / f"{stem}.json" if graph else None
    result = compile_project(
        project, llvm, directives, compiler_log,
        backend=backend, source_file=source_file, config=cfg,
    )
    provenance = {
        "schema_version": 1,
        "backend": backend.lower(),
        "project_dir": str(project),
        "source_file": str(source),
        "llvm_file": str(llvm),
        "directive_file": str(directives),
        "directive_semantics": result.directive_semantics,
        "frontend_fidelity": result.frontend_fidelity,
        "known_fidelity_gap": result.known_fidelity_gap,
        "compiler_version": result.compiler_version,
        "commands": [list(command) for command in result.commands],
        "ir_transforms": list(result.applied_transforms),
        "type_table": str(result.type_table) if result.type_table else None,
        "debug_llvm": str(result.debug_llvm) if result.debug_llvm else None,
    }
    _write_json(provenance_path, provenance)
    if graph_path is not None:
        graph_llvm(
            llvm, graph_path, directives, backend=backend, project_dir=project,
            config=cfg, provenance=provenance,
        )
    return ProjectArtifacts(llvm, directives, compiler_log, graph_path, provenance_path,
                            result.type_table, result.debug_llvm)
