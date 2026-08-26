"""ProGraML binary bridge."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from ..config import ProgramlConfig


def resolve_tool(configured: str, fallback_name: str) -> str:
    path = Path(configured).expanduser()
    if path.is_file():
        return str(path.resolve())
    discovered = shutil.which(configured)
    if discovered:
        return discovered
    workspace = (
        Path(__file__).resolve().parents[4]
        / "ir_parsing/ProGraML/build/lib/programl/bin"
        / fallback_name
    )
    if workspace.is_file():
        return str(workspace)
    legacy_workspace = (
        Path(__file__).resolve().parents[4]
        / "ir_parsing/ProGraML/bazel-bin/programl/bin"
        / fallback_name
    )
    if legacy_workspace.is_file():
        return str(legacy_workspace)
    raise FileNotFoundError(f"ProGraML tool not found: {configured}")


def graph_via_binary(llvm_path: Path, graph_path: Path, config: ProgramlConfig) -> None:
    llvm2graph = resolve_tool(config.llvm2graph, "llvm2graph-16")
    graph2json = resolve_tool(config.graph2json, "graph2json")
    ir_result = subprocess.run(
        [llvm2graph, "--stdout_fmt=pb", str(llvm_path)],
        capture_output=True,
        timeout=config.timeout_seconds,
    )
    if ir_result.returncode:
        raise RuntimeError(ir_result.stderr.decode(errors="replace")[:1000])
    json_result = subprocess.run(
        [graph2json, "--stdin_fmt=pb"],
        input=ir_result.stdout,
        capture_output=True,
        timeout=config.timeout_seconds,
    )
    if json_result.returncode:
        raise RuntimeError(json_result.stderr.decode(errors="replace")[:1000])
    graph_path.write_bytes(json_result.stdout)
