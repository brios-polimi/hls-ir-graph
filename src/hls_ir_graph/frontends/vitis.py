"""Vitis HLS frontend adapter."""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

from ..config import PreprocessConfig
from .base import CompileResult
from .common import (
    collapse_static_initializers,
    remove_generated_weight_initializers_text,
    run_command,
    strip_nonsemantic_metadata,
)


_UNSUPPORTED_RESOURCE_PRAGMA = re.compile(
    r"^[^\S\r\n]*#pragma\s+HLS\s+RESOURCE\b(?=[^\r\n]*\bcore\s*=\s*ROM_nP_BRAM\b)[^\r\n]*(?:\r?\n|$)",
    re.IGNORECASE | re.MULTILINE,
)


def remove_unsupported_resource_pragmas(preprocessed: Path) -> int:
    text = preprocessed.read_text()
    clean, count = remove_unsupported_resource_pragmas_text(text)
    if count:
        preprocessed.write_text(clean)
    return count


def remove_unsupported_resource_pragmas_text(text: str) -> tuple[str, int]:
    return _UNSUPPORTED_RESOURCE_PRAGMA.subn(
        lambda match: "\r\n" if match.group(0).endswith("\r\n") else "\n",
        text,
    )


def prepare_vitis_preprocessed(preprocessed: Path) -> tuple[int, int]:
    """Apply Vitis source cleanups with one file read and at most one write."""

    text = preprocessed.read_text()
    clean, resource_count = remove_unsupported_resource_pragmas_text(text)
    clean, weight_count = remove_generated_weight_initializers_text(clean)
    if resource_count or weight_count:
        preprocessed.write_text(clean)
    return resource_count, weight_count


@lru_cache(maxsize=None)
def vitis_compiler_version(clang: str, vitis_root: str) -> str | None:
    env = os.environ.copy()
    library_dir = Path(vitis_root) / "lib/lnx64.o"
    env["LD_LIBRARY_PATH"] = ":".join(
        item for item in (str(library_dir), env.get("LD_LIBRARY_PATH", "")) if item
    )
    output = subprocess.run(
        [clang, "--version"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    ).stdout.splitlines()
    return output[0] if output else None


def vitis_env(config: PreprocessConfig) -> dict[str, str]:
    env = os.environ.copy()
    library_dir = Path(config.vitis.root) / "lib/lnx64.o"
    env["LD_LIBRARY_PATH"] = ":".join(
        item for item in (str(library_dir), env.get("LD_LIBRARY_PATH", "")) if item
    )
    return env


def vitis_args(project_dir: Path, config: PreprocessConfig) -> list[str]:
    autopilot = Path(config.vitis.root) / "common/technology/autopilot"
    # -fhls supplies the synthesis macros and disables exceptions. The vendor
    # headers implement synthesis builtins; archived open-source ap_types are
    # simulation-only and must not shadow these headers (they #error in HLS).
    return [
        "-fhls", "-fno-threadsafe-statics", "-target", config.vitis.target,
        # Unlike the other HLS macros, -fhls does not define this backend marker.
        "-D__VITIS_HLS__", f"-I{autopilot}", f"-I{config.vitis.root}/include"
    ]


class VitisFrontend:
    def compile(
        self,
        project_dir: Path,
        source: Path,
        llvm_path: Path,
        directives_path: Path,
        compiler_log: Path,
        config: PreprocessConfig,
    ) -> CompileResult:
        clang = Path(config.vitis.clang)
        header = Path(config.vitis.root) / "common/technology/autopilot/ap_fixed.h"
        if not clang.is_file():
            raise FileNotFoundError(f"Required Vitis tool not found: {clang}")
        if not header.is_file():
            raise FileNotFoundError(f"Required Vitis header not found: {header}")

        env = vitis_env(config)
        args = vitis_args(project_dir, config)
        commands: list[tuple[str, ...]] = []
        with tempfile.TemporaryDirectory(prefix="vitis-ir-", dir=llvm_path.parent) as temp:
            work = Path(temp)
            preprocessed = work / "source.pp.cpp"
            raw_ll = work / "source.ll"
            preprocess = [str(clang), str(source), "-E", "-std=c++0x", *args, "-o", str(preprocessed)]
            commands.append(tuple(preprocess))
            run_command(preprocess, cwd=project_dir, env=env, timeout=config.compiler_timeout_seconds, stage="Vitis preprocessing")
            prepare_vitis_preprocessed(preprocessed)
            compile_command = [
                str(clang), str(preprocessed), "-S", "-emit-llvm", "-Wpragmas",
                "-Wdump-hls-pragmas", "-Wno-error=dump-hls-pragmas", *args,
                "-Xclang", "-no-opaque-pointers", "-o", str(raw_ll),
            ]
            commands.append(tuple(compile_command))
            result = run_command(compile_command, cwd=project_dir, env=env, timeout=config.compiler_timeout_seconds, stage="Vitis LLVM emission")
            directives_path.write_text(result.stderr)
            compiler_log.write_text(result.stdout)
            if not raw_ll.is_file() or not raw_ll.stat().st_size:
                raise RuntimeError("Vitis LLVM emission produced no textual IR")
            ir = raw_ll.read_text().replace("llvm.fpga.", "vitis.fpga.")
            llvm_path.write_text(strip_nonsemantic_metadata(collapse_static_initializers(ir)))

        return CompileResult(
            llvm_path, directives_path, compiler_log, tuple(commands),
            "compiler_reported", "backend_frontend",
            vitis_compiler_version(str(clang), config.vitis.root),
        )


_remove_unsupported_resource_pragmas = remove_unsupported_resource_pragmas
_prepare_vitis_preprocessed = prepare_vitis_preprocessed
_vitis_env = vitis_env
_vitis_args = vitis_args
_vitis_compiler_version = vitis_compiler_version
