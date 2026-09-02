"""Validated LLVM ``opt`` transform for Vitis graph input."""

from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

from ..graph.hierarchy import parse_llvm_hierarchy
from .base import IrTransformContext, register


_TARGET_TRIPLE = re.compile(r'^target triple = "[^"]+"$', re.MULTILINE)
_VITIS_INTRINSIC = re.compile(r"\bvitis\.fpga\.")
_PRAGMA_CARRIER = re.compile(r"\bllvm\.sideeffect\b")


def _hierarchy_signature(path: Path) -> dict:
    return {
        function: tuple(
            (block.name, tuple(sorted(block.successors))) for block in blocks
        )
        for function, blocks in parse_llvm_hierarchy(path).items()
    }


def _restore_target_triple(candidate: str, original: str) -> str:
    match = _TARGET_TRIPLE.search(original)
    if match is None:
        raise ValueError("Original LLVM IR has no target triple")
    restored, count = _TARGET_TRIPLE.subn(match.group(0), candidate, count=1)
    if count != 1:
        raise ValueError("Optimized LLVM IR has no unique target triple")
    return restored


@register("llvm_opt")
def llvm_opt(
    ir: str,
    *,
    context: IrTransformContext,
    passes: str,
    binary: str = "/usr/bin/opt-16",
    analysis_triple: str = "x86_64-unknown-linux-gnu",
    timeout_seconds: int = 360,
) -> str:
    """Apply conservative passes while preserving graph-critical structure."""

    passes = passes.strip()
    if not passes:
        return ir
    executable = Path(binary)
    if not executable.is_file():
        raise FileNotFoundError(f"Required LLVM opt tool not found: {executable}")

    with tempfile.TemporaryDirectory(prefix="hls-ir-opt-") as temp:
        work = Path(temp)
        original_path = work / "original.ll"
        optimized_path = work / "optimized.ll"
        original_path.write_text(ir)
        result = subprocess.run(
            [
                str(executable),
                "-S",
                "-opaque-pointers=0",
                f"-mtriple={analysis_triple}",
                f"-passes={passes}",
                str(original_path),
                "-o",
                str(optimized_path),
            ],
            cwd=context.project_dir,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        if result.returncode:
            detail = " ".join((result.stderr or result.stdout).split())[-1500:]
            raise RuntimeError(f"LLVM opt failed (exit {result.returncode}): {detail}")
        optimized = _restore_target_triple(optimized_path.read_text(), ir)
        optimized_path.write_text(optimized)

        if _hierarchy_signature(optimized_path) != _hierarchy_signature(original_path):
            raise RuntimeError("LLVM opt changed the function/block CFG signature")
        for label, pattern in (
            ("Vitis intrinsic", _VITIS_INTRINSIC),
            ("pragma carrier", _PRAGMA_CARRIER),
        ):
            before = len(pattern.findall(ir))
            after = len(pattern.findall(optimized))
            if before != after:
                raise RuntimeError(
                    f"LLVM opt changed {label} count: {before} -> {after}"
                )
        return optimized
