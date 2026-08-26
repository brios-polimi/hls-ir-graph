"""Shared frontend execution and explicit LLVM policy transforms."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


_STATIC_INITIALIZER = re.compile(
    r"^(?P<header>define\s+internal\s+void\s+@__cxx_global_var_init(?:\.\d+)?\(\)[^{]*\{).*?^\}",
    re.MULTILINE | re.DOTALL,
)
_LINE_MARKER = re.compile(r'(?m)^#\s+\d+\s+"(?P<path>[^"]+)"[^\n]*\n')
_WEIGHT_INITIALIZER = re.compile(
    r"(?m)^(?P<declaration>[^\n;{}]*\[[^\n;{}]+\])\s*=\s*\{[^\n;]*\};"
)


def failure_detail(output: str, limit: int = 1500) -> str:
    lines = output.strip().splitlines()
    errors = [line.strip() for line in lines if re.search(r"\b(?:fatal )?error:", line, re.I)]
    detail = " | ".join(errors) if errors else " ".join(lines)
    return " ".join(detail.split())[-limit:]


def run_command(
    command: list[str], *, cwd: Path, timeout: int, stage: str, env=None
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout
    )
    if result.returncode:
        raise RuntimeError(
            f"{stage} failed (exit {result.returncode}): "
            f"{failure_detail(result.stderr or result.stdout)}"
        )
    return result


def remove_generated_weight_initializers(preprocessed: Path) -> int:
    text = preprocessed.read_text()
    markers = list(_LINE_MARKER.finditer(text))
    pieces: list[str] = []
    cursor = 0
    count = 0
    for index, marker in enumerate(markers):
        start = marker.end()
        end = markers[index + 1].start() if index + 1 < len(markers) else len(text)
        pieces.append(text[cursor:start])
        segment = text[start:end]
        source = Path(marker.group("path"))
        if source.suffix == ".h" and source.parent.name == "weights":
            segment, removed = _WEIGHT_INITIALIZER.subn(
                lambda match: f"{match.group('declaration').rstrip()};", segment
            )
            count += removed
        pieces.append(segment)
        cursor = end
    if count:
        preprocessed.write_text("".join(pieces))
    return count


def collapse_static_initializers(ir: str) -> str:
    return _STATIC_INITIALIZER.sub(
        lambda match: f"{match.group('header')}\nentry:\n  ret void\n}}", ir
    )


def strip_nonsemantic_metadata(ir: str) -> str:
    ir = re.sub(r", ![A-Za-z0-9_.]+ !\d+", "", ir)
    ir = re.sub(r"\s+![A-Za-z0-9_.]+ !\d+", "", ir)
    ir = re.sub(
        r"^\s*(?:tail )?call void @llvm\.(?:dbg\.[^(]+|experimental\.noalias\.scope\.decl)\(.*\)(?: #\d+)?\s*\n",
        "",
        ir,
        flags=re.MULTILINE,
    )
    ir = re.sub(r"^declare void @llvm\.dbg\.[^\n]*\n", "", ir, flags=re.MULTILINE)
    return re.sub(r"^!.*(?:\n|$)", "", ir, flags=re.MULTILINE)


# Compatibility aliases retained for migrated tests and downstream callers.
_failure_detail = failure_detail
_remove_generated_weight_initializers = remove_generated_weight_initializers
_collapse_static_initializers = collapse_static_initializers
_strip_nonsemantic_metadata = strip_nonsemantic_metadata
