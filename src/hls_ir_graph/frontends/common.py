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
_WEIGHT_NAME = re.compile(r"\b(?P<name>[A-Za-z_]\w*)\s*\[[^\n;{}]+\]\s*$")


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


def remove_generated_weight_initializers(
    preprocessed: Path, *, weight_names: set[str] | None = None
) -> int:
    text = preprocessed.read_text()
    clean, count = remove_generated_weight_initializers_text(
        text, weight_names=weight_names
    )
    if count:
        preprocessed.write_text(clean)
    return count


def remove_generated_weight_initializers_text(
    text: str, *, weight_names: set[str] | None = None
) -> tuple[str, int]:
    """Return preprocessed source with generated weight values removed."""

    markers = list(_LINE_MARKER.finditer(text))
    if not markers:
        return text, 0
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
            if weight_names is not None:
                for declaration in _WEIGHT_INITIALIZER.finditer(segment):
                    name = _WEIGHT_NAME.search(declaration.group("declaration"))
                    if name:
                        weight_names.add(name.group("name"))
            segment, removed = _WEIGHT_INITIALIZER.subn(
                lambda match: f"{match.group('declaration').rstrip()};", segment
            )
            count += removed
        pieces.append(segment)
        cursor = end
    return "".join(pieces), count


def compact_zero_weight_initializers(ir: str, weight_names: set[str]) -> str:
    """Replace expanded zero globals for weight arrays with ``zeroinitializer``."""

    if not weight_names:
        return ir

    compacted: list[str] = []
    for line in ir.splitlines(keepends=True):
        symbol = re.match(r"^@(?P<name>[A-Za-z_]\w*)\s*=", line)
        if not symbol or symbol.group("name") not in weight_names:
            compacted.append(line)
            continue

        global_match = re.search(r"\bglobal\s+", line)
        if not global_match:
            compacted.append(line)
            continue
        type_start = global_match.end()
        if type_start >= len(line) or line[type_start] != "[":
            compacted.append(line)
            continue

        depth = 0
        type_end = None
        for index in range(type_start, len(line)):
            if line[index] == "[":
                depth += 1
            elif line[index] == "]":
                depth -= 1
                if depth == 0:
                    type_end = index + 1
                    break
        if type_end is None or not line[type_end:].lstrip().startswith("["):
            compacted.append(line)
            continue

        newline = "\n" if line.endswith("\n") else ""
        compacted.append(line[:type_end] + " zeroinitializer" + newline)
    return "".join(compacted)


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
_compact_zero_weight_initializers = compact_zero_weight_initializers
_collapse_static_initializers = collapse_static_initializers
_strip_nonsemantic_metadata = strip_nonsemantic_metadata
