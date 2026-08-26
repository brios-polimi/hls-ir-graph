#!/usr/bin/env python3
"""Write a small hls4ml Bambu project for preprocessing smoke tests."""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml


class _ConfigLoader(yaml.SafeLoader):
    pass


def _keras_model(loader, node):
    return loader.construct_scalar(node)


_ConfigLoader.add_constructor("!keras_model", _keras_model)


def _reference_values(path: Path | None) -> tuple[str, float, str | None]:
    if path is None:
        return "xc7a100tcsg324-1", 5.0, None
    with path.open() as handle:
        config = yaml.load(handle, Loader=_ConfigLoader)
    model_path = config.get("KerasModel")
    return (
        str(config.get("Part", "xc7a100tcsg324-1")),
        float(config.get("ClockPeriod", 5)),
        model_path,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--reference-config", type=Path)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise SystemExit(f"Refusing to overwrite existing output: {args.output_dir}")

    import tensorflow as tf
    import hls4ml

    part, clock_period, referenced_model = _reference_values(args.reference_config)
    if referenced_model and not Path(referenced_model).is_file():
        print(
            "reference config is not self-contained: missing KerasModel "
            f"{referenced_model}; using a deterministic smoke model"
        )

    tf.keras.utils.set_random_seed(7)
    model = tf.keras.Sequential(
        [
            tf.keras.layers.Input(shape=(4,), name="model_input"),
            tf.keras.layers.Dense(3, activation="relu", name="dense_1"),
            tf.keras.layers.Dense(2, name="dense_2"),
        ]
    )
    hls_config = hls4ml.utils.config_from_keras_model(
        model, granularity="name", backend="Bambu"
    )
    hls_config["Model"]["Precision"] = "fixed<16,6>"
    hls_model = hls4ml.converters.convert_from_keras_model(
        model,
        hls_config=hls_config,
        backend="Bambu",
        output_dir=str(args.output_dir),
        project_name="myproject",
        io_type="io_parallel",
        part=part,
        clock_period=clock_period,
    )
    hls_model.write()
    print(args.output_dir.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
