"""Small registry for explicit, provenance-recorded LLVM transformations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class IrTransformContext:
    backend: str
    project_dir: Path
    source_file: Path
    directives_file: Path


IrTransform = Callable[..., str]
_TRANSFORMS: dict[str, IrTransform] = {}


def register(name: str):
    """Register a pure textual LLVM transform under a stable name."""

    def decorator(transform: IrTransform):
        if name in _TRANSFORMS:
            raise KeyError(f"LLVM transform already registered: {name}")
        _TRANSFORMS[name] = transform
        return transform

    return decorator


def _parse_spec(spec: str | dict) -> tuple[str, dict]:
    if isinstance(spec, str):
        return spec, {}
    unknown = set(spec) - {"name", "options"}
    if unknown or "name" not in spec:
        raise ValueError(f"Invalid LLVM transform specification: {spec}")
    options = spec.get("options", {})
    if not isinstance(options, dict):
        raise TypeError("LLVM transform options must be an object")
    return str(spec["name"]), options


def apply_transforms(
    llvm_path: Path,
    specs: list[str | dict],
    context: IrTransformContext,
) -> list[dict]:
    """Apply configured transforms in order and return provenance records."""

    ir = llvm_path.read_text()
    applied: list[dict] = []
    for spec in specs:
        name, options = _parse_spec(spec)
        if name not in _TRANSFORMS:
            raise KeyError(f"Unknown LLVM transform {name!r}; available: {sorted(_TRANSFORMS)}")
        ir = _TRANSFORMS[name](ir, context=context, **options)
        if not isinstance(ir, str):
            raise TypeError(f"LLVM transform {name!r} did not return text")
        applied.append({"name": name, "options": options})
    if applied:
        llvm_path.write_text(ir)
    return applied
