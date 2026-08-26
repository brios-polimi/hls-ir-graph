"""Command-line interface for standalone HLS project preprocessing."""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

from .config import PreprocessConfig
from .pipeline import preprocess_project


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compile a written HLS project to LLVM and ProGraML JSON")
    parser.add_argument("project_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--backend", choices=("bambu", "vitis"), required=True)
    parser.add_argument("--source", default=None)
    parser.add_argument("--clang", help="Clang used by the portable Bambu adapter")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--skip-graph", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = PreprocessConfig.from_file(args.config) if args.config else PreprocessConfig()
    if args.clang:
        config.bambu.clang = args.clang
    try:
        artifacts = preprocess_project(
            args.project_dir, args.output_dir, backend=args.backend,
            source_file=args.source, config=config, graph=not args.skip_graph,
        )
    except (FileNotFoundError, RuntimeError, subprocess.TimeoutExpired, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for key, value in asdict(artifacts).items():
        if value is not None:
            print(f"{key}: {value}")
    return 0
