"""Dataset-independent tool configuration."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class ProgramlConfig:
    llvm2graph: str = "llvm2graph-16"
    graph2json: str = "graph2json"
    timeout_seconds: int = 300


@dataclass
class VitisConfig:
    root: str = "/opt/Xilinx/2025.2/Vitis"
    clang: str = "/opt/Xilinx/2025.2/lnx64/tools/clang-16/bin/clang"
    target: str = "fpga64-xilinx-linux-gnu"


@dataclass
class BambuConfig:
    clang: str = "clang-16"
    mirror_optimization_flags: bool = True


@dataclass
class PreprocessConfig:
    source_file: str = "firmware/myproject.cpp"
    compiler_timeout_seconds: int = 360
    programl: ProgramlConfig = field(default_factory=ProgramlConfig)
    vitis: VitisConfig = field(default_factory=VitisConfig)
    bambu: BambuConfig = field(default_factory=BambuConfig)
    ir_transforms: list[str | dict] = field(default_factory=list)

    # Compatibility properties used by the Vitis pragma enrichment code. The
    # public configuration stays backend-scoped while this avoids duplicating
    # the thoroughly tested enrichment implementation during extraction.
    @property
    def hls_source_file(self) -> str:
        return self.source_file

    @property
    def vitis_root(self) -> str:
        return self.vitis.root

    @property
    def vitis_clang_binary(self) -> str:
        return self.vitis.clang

    @property
    def vitis_target(self) -> str:
        return self.vitis.target

    @property
    def vitis_timeout_seconds(self) -> int:
        return self.compiler_timeout_seconds

    @property
    def programl_binary(self) -> str:
        return self.programl.llvm2graph

    @programl_binary.setter
    def programl_binary(self, value: str) -> None:
        self.programl.llvm2graph = value

    @property
    def programl_to_json_binary(self) -> str:
        return self.programl.graph2json

    @programl_to_json_binary.setter
    def programl_to_json_binary(self, value: str) -> None:
        self.programl.graph2json = value

    @classmethod
    def from_file(cls, path: str | Path) -> "PreprocessConfig":
        data = json.loads(Path(path).read_text())
        allowed = {
            "source_file", "compiler_timeout_seconds", "programl", "vitis",
            "bambu", "ir_transforms",
        }
        unknown = set(data) - allowed
        if unknown:
            raise ValueError(f"Unknown preprocessing config keys: {sorted(unknown)}")
        return cls(
            source_file=data.get("source_file", cls.source_file),
            compiler_timeout_seconds=data.get(
                "compiler_timeout_seconds", cls.compiler_timeout_seconds
            ),
            programl=ProgramlConfig(**data.get("programl", {})),
            vitis=VitisConfig(**data.get("vitis", {})),
            bambu=BambuConfig(**data.get("bambu", {})),
            ir_transforms=data.get("ir_transforms", []),
        )

    def to_file(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(asdict(self), indent=2) + "\n")
