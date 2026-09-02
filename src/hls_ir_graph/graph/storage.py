"""Lossless-for-training graph storage compaction."""

from __future__ import annotations

import re


NODE_CONSTANT = 2
NODE_PRAGMA = 3
STORAGE_SCHEMA_VERSION = 1
_SCALAR_CONSTANT_TYPE = re.compile(r"^(?:i\d+|half|float|double)$")


def compact_vitis_graph(graph: dict, *, retain_full_text: bool) -> dict:
    """Drop visualization-only text after all Vitis enrichment is complete."""

    full_text_removed = 0
    repeated_metadata_removed = 0
    if not retain_full_text:
        for node in graph.get("nodes", []):
            keep = (
                int(node.get("type", -1)) == NODE_CONSTANT
                and _SCALAR_CONSTANT_TYPE.fullmatch(str(node.get("text", "")))
            )
            features = node.get("features")
            if not isinstance(features, dict):
                continue
            if not keep and "full_text" in features:
                del features["full_text"]
                full_text_removed += 1
            if "injector" in features:
                del features["injector"]
                repeated_metadata_removed += 1
            if int(node.get("type", -1)) != NODE_PRAGMA and "schema_version" in features:
                del features["schema_version"]
                repeated_metadata_removed += 1
            if not features:
                del node["features"]

    stats = {
        "schema_version": STORAGE_SCHEMA_VERSION,
        "full_text": "all" if retain_full_text else "scalar_constants_only",
        "full_text_fields_removed": full_text_removed,
        "repeated_metadata_fields_removed": repeated_metadata_removed,
    }
    graph["storage"] = stats
    return stats
