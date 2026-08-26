"""Portable Clang adapter for hls4ml projects written for Bambu."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from ..config import PreprocessConfig
from .base import CompileResult
from .common import remove_generated_weight_initializers, run_command, strip_nonsemantic_metadata


_LINE_MARKER = re.compile(r'^#\s+(?P<line>\d+)\s+"(?P<path>[^"]+)"')
_HLS_PRAGMA = re.compile(
    r"^\s*#pragma\s+HLS(?:\s+|_)(?P<directive>[A-Za-z_][A-Za-z0-9_]*)(?P<options>.*)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SourceDirective:
    backend: str
    origin: str
    source_file: str
    source_line: int
    directive: str
    options: str
    text: str


def extract_source_directives(preprocessed: str | Path, *, backend: str) -> list[SourceDirective]:
    current_path = ""
    current_line = 0
    records: list[SourceDirective] = []
    for raw_line in Path(preprocessed).read_text(errors="replace").splitlines():
        marker = _LINE_MARKER.match(raw_line)
        if marker:
            current_path = marker.group("path")
            current_line = int(marker.group("line"))
            continue
        pragma = _HLS_PRAGMA.match(raw_line)
        if pragma and current_path and not current_path.startswith("<"):
            records.append(SourceDirective(
                backend=backend, origin="preprocessed_source", source_file=current_path,
                source_line=current_line, directive=pragma.group("directive").lower(),
                options=pragma.group("options").strip(), text=raw_line.strip(),
            ))
        current_line += 1
    return records


def bambu_args(project_dir: Path) -> list[str]:
    ac_types = project_dir / "firmware/ac_types"
    if not ac_types.is_dir():
        raise FileNotFoundError(
            f"Bambu ac_types directory not found: {ac_types}. Generate the project with the hls4ml Bambu writer first."
        )
    return [
        "-std=c++14", "-D__NO_INLINE__", "-D__SYNTHESIS__", "-D__BAMBU__",
        f"-I{ac_types}", "-ftemplate-depth=2048", "-m64",
    ]


class BambuFrontend:
    def compile(
        self,
        project_dir: Path,
        source: Path,
        llvm_path: Path,
        directives_path: Path,
        compiler_log: Path,
        config: PreprocessConfig,
    ) -> CompileResult:
        configured = config.bambu.clang
        clang = shutil.which(configured) if "/" not in configured else configured
        if not clang or not Path(clang).is_file():
            raise FileNotFoundError(f"Bambu-compatible Clang not found: {configured}")
        args = bambu_args(project_dir)
        commands: list[tuple[str, ...]] = []
        with tempfile.TemporaryDirectory(prefix="bambu-ir-", dir=llvm_path.parent) as temp:
            work = Path(temp)
            preprocessed = work / "source.pp.cpp"
            raw_ll = work / "source.ll"
            preprocess_command = [clang, str(source), "-E", *args, "-o", str(preprocessed)]
            commands.append(tuple(preprocess_command))
            preprocess = run_command(preprocess_command, cwd=project_dir, timeout=config.compiler_timeout_seconds, stage="Bambu preprocessing")
            directives = extract_source_directives(preprocessed, backend="bambu")
            directives_path.write_text("".join(json.dumps(asdict(record), sort_keys=True) + "\n" for record in directives))
            remove_generated_weight_initializers(preprocessed)
            compile_command = [
                clang, str(preprocessed), "-S", "-emit-llvm", *args,
                "-Xclang", "-no-opaque-pointers", "-Wno-unknown-pragmas",
                "-Wno-tautological-compare", "-O2", "-fno-builtin-bcmp",
                "-fno-builtin-memcpy", "-fno-builtin-memmove", "-fno-builtin-memset",
                "-fno-exceptions", "-ffp-contract=off", "-finline-functions",
                "-fno-slp-vectorize", "-fno-stack-protector", "-fno-threadsafe-statics",
                "-fno-unroll-loops", "-fno-use-cxa-atexit", "-fno-vectorize",
                "-fwrapv", "-o", str(raw_ll),
            ]
            commands.append(tuple(compile_command))
            compiled = run_command(compile_command, cwd=project_dir, timeout=config.compiler_timeout_seconds, stage="Bambu LLVM emission")
            compiler_log.write_text(preprocess.stderr + preprocess.stdout + compiled.stderr + compiled.stdout)
            if not raw_ll.is_file() or not raw_ll.stat().st_size:
                raise RuntimeError("Bambu LLVM emission produced no textual IR")
            llvm_path.write_text(strip_nonsemantic_metadata(raw_ll.read_text()))
        version = subprocess.run([clang, "--version"], capture_output=True, text=True, check=False).stdout.splitlines()
        return CompileResult(
            llvm_path, directives_path, compiler_log, tuple(commands),
            "source_requested", "portable_clang_approximation", version[0] if version else None,
            "Bambu custom Clang plugins and internal lowering passes are not applied",
        )


_bambu_args = bambu_args
