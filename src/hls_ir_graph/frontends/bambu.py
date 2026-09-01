"""Portable Clang adapter for hls4ml projects written for Bambu."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from ..config import PreprocessConfig
from .base import CompileResult
from .common import (
    collapse_static_initializers,
    compact_zero_weight_initializers,
    remove_generated_weight_initializers,
    run_command,
    strip_nonsemantic_metadata,
)
from .debug_types import recover_debug_types


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
        "-D__SYNTHESIS__", "-D__BAMBU__", f"-I{ac_types}",
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
        # Keep the source-language policy consistent across preprocessing and
        # codegen. These affect semantics, not just optimization heuristics.
        language = ["-std=c++14", "-fno-exceptions", "-fno-threadsafe-statics",
                    "-fwrapv", "-ffp-contract=off", "-m64"]
        debug_llvm = llvm_path.with_suffix(".debug.ll")
        type_table = llvm_path.with_suffix(".types.json")
        commands: list[tuple[str, ...]] = []
        with tempfile.TemporaryDirectory(prefix="bambu-ir-", dir=llvm_path.parent) as temp:
            work = Path(temp)
            preprocessed = work / "source.pp.cpp"
            raw_ll = work / "source.ll"
            preprocess_command = [clang, str(source), "-E", *args, *language, "-o", str(preprocessed)]
            commands.append(tuple(preprocess_command))
            preprocess = run_command(preprocess_command, cwd=project_dir, timeout=config.compiler_timeout_seconds, stage="Bambu preprocessing")
            directives = extract_source_directives(preprocessed, backend="bambu")
            directives_path.write_text("".join(json.dumps(asdict(record), sort_keys=True) + "\n" for record in directives))
            weight_names: set[str] = set()
            remove_generated_weight_initializers(preprocessed, weight_names=weight_names)
            compile_command = [
                clang, str(preprocessed), "-S", "-emit-llvm", *language,
                "-Xclang", "-no-opaque-pointers", "-Wno-unknown-pragmas",
                # Even -O0 normally runs always-inline passes. Capture the
                # frontend module before those can erase debug associations.
                "-O0", "-Xclang", "-disable-llvm-passes",
                # Do not make later explicitly configured transforms no-ops.
                "-Xclang", "-disable-O0-optnone",
                "-g", "-fstandalone-debug", "-ftemplate-depth=2048",
                "-o", str(raw_ll),
            ]
            commands.append(tuple(compile_command))
            compiled = run_command(compile_command, cwd=project_dir, timeout=config.compiler_timeout_seconds, stage="Bambu LLVM emission")
            compiler_log.write_text(preprocess.stderr + preprocess.stdout + compiled.stderr + compiled.stdout)
            if not raw_ll.is_file() or not raw_ll.stat().st_size:
                raise RuntimeError("Bambu LLVM emission produced no textual IR")
            raw = raw_ll.read_text()
            debug_llvm.write_text(raw)
            ir, table = recover_debug_types(raw)
            table['debug_llvm'] = str(debug_llvm)
            table['debug_llvm_sha256'] = hashlib.sha256(raw.encode()).hexdigest()
            table['stage'] = 'frontend_before_configured_ir_transforms'
            type_table.write_text(json.dumps(table, indent=2) + "\n")
            commands.append(("debug-type-recovery-v1", str(debug_llvm), str(type_table)))
            if config.bambu.require_complete_ac_types and not table['complete_ac_mapping']:
                raise RuntimeError(
                    f"Incomplete AC debug type mapping: {table['mapped_ac_type_count']}/"
                    f"{table['ac_type_count']}; inspect {type_table}"
                )
            ir = strip_nonsemantic_metadata(ir)
            ir = collapse_static_initializers(ir)
            llvm_path.write_text(compact_zero_weight_initializers(ir, weight_names))
        version = subprocess.run([clang, "--version"], capture_output=True, text=True, check=False).stdout.splitlines()
        return CompileResult(
            llvm_path, directives_path, compiler_log, tuple(commands),
            "source_requested", "portable_clang_approximation", version[0] if version else None,
            "Bambu custom Clang plugins and internal lowering passes are not applied; "
            "LLVM record names recovered from debug metadata before optimization",
            type_table=type_table, debug_llvm=debug_llvm,
        )


_bambu_args = bambu_args
