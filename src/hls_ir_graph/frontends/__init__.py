"""Compilation frontend adapters."""

from .bambu import BambuFrontend
from .vitis import VitisFrontend

FRONTENDS = {"bambu": BambuFrontend, "vitis": VitisFrontend}

__all__ = ["BambuFrontend", "VitisFrontend", "FRONTENDS"]
