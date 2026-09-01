"""Frontend adapter contracts shared by all HLS backends."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..config import PreprocessConfig


@dataclass(frozen=True)
class CompileResult:
    """Files and fidelity metadata produced by one frontend invocation."""

    llvm: Path
    directives: Path
    compiler_log: Path
    commands: tuple[tuple[str, ...], ...]
    directive_semantics: str
    frontend_fidelity: str
    compiler_version: str | None = None
    known_fidelity_gap: str | None = None
    applied_transforms: tuple[dict, ...] = ()
    type_table: Path | None = None
    debug_llvm: Path | None = None


class Frontend(Protocol):
    def compile(
        self,
        project_dir: Path,
        source: Path,
        llvm_path: Path,
        directives_path: Path,
        compiler_log: Path,
        config: PreprocessConfig,
    ) -> CompileResult: ...
