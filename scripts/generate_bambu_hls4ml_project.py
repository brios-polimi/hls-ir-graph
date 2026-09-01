#!/usr/bin/env python3
"""Recover an hls4ml project from a source YAML file and archived Keras model."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import shutil
import sys
from pathlib import Path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Write a fresh hls4ml Bambu project from an archived model"
    )
    parser.add_argument("archive_project", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument(
        "--hls4ml-root",
        type=Path,
        default=Path("/home/brend/hls4ml-dev/hls4ml-bambu/hls4ml"),
        help="Checkout containing the hls4ml package with the Bambu backend",
    )
    parser.add_argument("--project-name", default="myproject")
    return parser


def _find_model(archive_project: Path) -> Path:
    candidates = (
        archive_project / "model_ir/keras_model.keras.gz",
        archive_project / "model_ir/keras_model.keras",
        archive_project / "model_ir/keras_model.h5.gz",
        archive_project / "model_ir/keras_model.h5",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        "No compressed or uncompressed model_ir/keras_model.{keras,h5} found in "
        f"{archive_project}"
    )


def _materialize_model(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.name.endswith(".gz"):
        with gzip.open(source, "rb") as input_file, destination.open("wb") as output_file:
            shutil.copyfileobj(input_file, output_file)
    else:
        shutil.copyfile(source, destination)


def _load_model(model_path: Path):
    import keras

    custom_objects = {}
    try:
        from qkeras.utils import _add_supported_quantized_objects
    except ImportError:
        pass
    else:
        _add_supported_quantized_objects(custom_objects)
    return keras.models.load_model(model_path, custom_objects=custom_objects, compile=False)


def generate(archive_project: Path, output_dir: Path, hls4ml_root: Path, project_name: str) -> Path:
    archive_project = archive_project.resolve()
    output_dir = output_dir.resolve()
    hls4ml_root = hls4ml_root.resolve()
    config_path = archive_project / "hls4ml_config.yml"
    if not config_path.is_file():
        raise FileNotFoundError(f"hls4ml_config.yml not found in {archive_project}")
    if not hls4ml_root.is_dir():
        raise FileNotFoundError(f"hls4ml checkout not found: {hls4ml_root}")

    project_dir = output_dir / "hls4ml-project"
    if project_dir.exists():
        raise FileExistsError(f"Refusing to overwrite existing output: {project_dir}")
    project_dir.mkdir(parents=True, exist_ok=True)

    model_source = _find_model(archive_project)
    model_path = output_dir / model_source.name.removesuffix(".gz")
    _materialize_model(model_source, model_path)
    shutil.copyfile(config_path, output_dir / "source_hls4ml_config.yml")

    sys.path.insert(0, str(hls4ml_root))
    import yaml
    from hls4ml.converters import convert_from_config

    # The archive tag points to the original machine's path. Parse the YAML as
    # ordinary data, then replace that field with the model loaded above.
    config = yaml.safe_load(config_path.read_text().replace("!keras_model", ""))
    config.update(
        {
            "Backend": "Bambu",
            "OutputDir": str(project_dir),
            "ProjectName": project_name,
            "KerasModel": _load_model(model_path),
        }
    )

    model = convert_from_config(config)
    model.write()

    summary = {
        "backend": model.config.backend.name,
        "archive_project": str(archive_project),
        "source_config": str(config_path),
        "model_source": str(model_source),
        "model_sha256": hashlib.sha256(model_path.read_bytes()).hexdigest(),
        "model_file": str(model_path),
        "hls4ml_root": str(hls4ml_root),
        "project_dir": str(project_dir),
        "project_name": project_name,
        "keras_layers": [layer.__class__.__name__ for layer in config["KerasModel"].layers],
    }
    (output_dir / "recovery.json").write_text(json.dumps(summary, indent=2) + "\n")
    return project_dir


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        project_dir = generate(
            args.archive_project,
            args.output_dir,
            args.hls4ml_root,
            args.project_name,
        )
    except (FileExistsError, FileNotFoundError, ImportError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"hls4ml project: {project_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
