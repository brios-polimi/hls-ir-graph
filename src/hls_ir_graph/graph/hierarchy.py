"""Enrich ProGraML JSON with LLVM function/basic-block hierarchy and CFG."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
import re

from .intrinsics import is_intrinsic_function_symbol


NODE_INSTRUCTION = 0
NODE_BLOCK = 4
NODE_FUNCTION = 5
FLOW_CONTROL = 0
FLOW_BLOCK = 4
FLOW_FUNCTION = 5
HIERARCHY_SCHEMA_VERSION = 2
HIERARCHY_INJECTOR = "hls_ir_graph.llvm_hierarchy"

_LLVM_FUNCTION_RE = re.compile(
    r'^define\b.*?@(?:"(?P<quoted>(?:\\.|[^"])*)"|'
    r"(?P<plain>[-$._A-Za-z0-9]+))\("
)
_LLVM_BLOCK_RE = re.compile(
    r'^\s*(?P<name>"(?:\\.|[^"])*"|[-$._A-Za-z0-9]+|[0-9]+):'
    r"\s*(?:;.*)?$"
)
_LLVM_LABEL_REF_RE = re.compile(
    r'label\s+%(?:"(?P<quoted>(?:\\.|[^"])*)"|'
    r"(?P<plain>[-$._A-Za-z0-9]+|[0-9]+))"
)
_TERMINATOR_RE = re.compile(
    r"^\s*(?:%[^=]+?\s*=\s*)?"
    r"(?:br|switch|indirectbr|invoke|callbr|ret|resume|catchswitch|"
    r"catchret|cleanupret|unreachable)\b"
)


@dataclass(frozen=True)
class LlvmBlock:
    name: str
    successors: tuple[str, ...]


def _unquote(value: str) -> str:
    return value[1:-1] if value.startswith('"') else value


def _successors(lines: list[str]) -> tuple[str, ...]:
    """Return labels referenced by the block terminator, never by PHI nodes."""

    start = next(
        (index for index in range(len(lines) - 1, -1, -1) if _TERMINATOR_RE.match(lines[index])),
        None,
    )
    if start is None:
        return ()
    terminator = "\n".join(lines[start:])
    return tuple(
        dict.fromkeys(
            match.group("quoted") or match.group("plain")
            for match in _LLVM_LABEL_REF_RE.finditer(terminator)
        )
    )


def parse_llvm_hierarchy(llvm_path: str | Path) -> dict[str, tuple[LlvmBlock, ...]]:
    """Parse defined functions, ordered blocks, and terminator CFG successors."""

    functions: dict[str, tuple[LlvmBlock, ...]] = {}
    function = ""
    blocks: list[LlvmBlock] = []
    block_name = "entry"
    block_lines: list[str] = []
    block_is_implicit = True

    def finish_block() -> None:
        nonlocal block_lines
        if block_lines:
            blocks.append(LlvmBlock(block_name, _successors(block_lines)))
        block_lines = []

    with Path(llvm_path).open(errors="replace") as llvm:
        for raw_line in llvm:
            if not function:
                match = _LLVM_FUNCTION_RE.match(raw_line)
                if match:
                    function = match.group("quoted") or match.group("plain")
                    blocks = []
                    block_name = "entry"
                    block_lines = []
                    block_is_implicit = True
                continue

            if raw_line.strip() == "}":
                finish_block()
                functions[function] = tuple(blocks)
                function = ""
                continue

            block_match = _LLVM_BLOCK_RE.match(raw_line.rstrip("\n"))
            if block_match:
                explicit_name = _unquote(block_match.group("name"))
                if block_is_implicit and not block_lines:
                    block_name = explicit_name
                else:
                    finish_block()
                    block_name = explicit_name
                block_is_implicit = False
                continue

            stripped = raw_line.strip()
            if stripped and not stripped.startswith(";"):
                block_lines.append(raw_line.rstrip("\n"))

    return functions


def add_llvm_hierarchy(
    graph: dict,
    nodes: list[dict],
    links: list[dict],
    llvm_path: str | Path,
    source_loop_labels: set[str],
) -> tuple[dict[tuple[int, str], dict], dict[int, dict], dict]:
    """Add function/block nodes, membership edges, and the LLVM block CFG."""

    parsed = parse_llvm_hierarchy(llvm_path)
    function_records = graph.get("graph", {}).get("function", [])
    function_names = [str(item.get("name", "")).lstrip("@") for item in function_records]

    instructions_by_function_block: dict[tuple[int, int], list[dict]] = defaultdict(list)
    block_order: dict[int, list[int]] = defaultdict(list)
    seen_blocks: dict[int, set[int]] = defaultdict(set)
    for node in nodes:
        if int(node.get("type", -1)) != NODE_INSTRUCTION or node.get("text") == "[external]":
            continue
        function_id = int(node.get("function", -1))
        block_id = int(node.get("block", -1))
        instructions_by_function_block[(function_id, block_id)].append(node)
        if block_id not in seen_blocks[function_id]:
            seen_blocks[function_id].add(block_id)
            block_order[function_id].append(block_id)

    function_lookup: dict[int, dict] = {}
    for function_id, symbol in enumerate(function_names):
        if is_intrinsic_function_symbol(symbol):
            continue
        function_node = {
            "block": -1,
            "features": {
                "schema_version": [str(HIERARCHY_SCHEMA_VERSION)],
                "injector": [HIERARCHY_INJECTOR],
                "name": [symbol],
                "is_defined": ["true" if symbol in parsed else "false"],
            },
            "function": function_id,
            "id": len(nodes),
            "text": "llvm.function",
            "type": NODE_FUNCTION,
        }
        nodes.append(function_node)
        function_lookup[function_id] = function_node

    block_lookup: dict[tuple[int, str], dict] = {}
    mapping_failures: list[dict] = []
    mapped_functions = 0
    cfg_edges = 0
    instruction_membership_edges = 0
    function_membership_edges = 0
    cfg_validation_failures: list[dict] = []

    for function_id, symbol in enumerate(function_names):
        llvm_blocks = parsed.get(symbol)
        graph_blocks = block_order.get(function_id, [])
        if llvm_blocks is None:
            continue
        if len(llvm_blocks) != len(graph_blocks):
            mapping_failures.append(
                {
                    "function": function_id,
                    "symbol": symbol,
                    "llvm_blocks": len(llvm_blocks),
                    "graph_blocks": len(graph_blocks),
                }
            )
            continue

        mapped_functions += 1
        function_node = function_lookup[function_id]
        for llvm_block, graph_block in zip(llvm_blocks, graph_blocks):
            block_node = {
                "block": graph_block,
                "features": {
                    "schema_version": [str(HIERARCHY_SCHEMA_VERSION)],
                    "injector": [HIERARCHY_INJECTOR],
                    "name": [llvm_block.name],
                    "is_source_loop": [
                        "true" if llvm_block.name in source_loop_labels else "false"
                    ],
                },
                "function": function_id,
                "id": len(nodes),
                "text": "llvm.basic_block",
                "type": NODE_BLOCK,
            }
            nodes.append(block_node)
            block_lookup[(function_id, llvm_block.name)] = block_node

            links.extend(
                (
                    _link(FLOW_FUNCTION, function_node["id"], block_node["id"]),
                    _link(FLOW_FUNCTION, block_node["id"], function_node["id"]),
                )
            )
            function_membership_edges += 2
            for instruction in instructions_by_function_block[(function_id, graph_block)]:
                links.extend(
                    (
                        _link(FLOW_BLOCK, block_node["id"], instruction["id"]),
                        _link(FLOW_BLOCK, instruction["id"], block_node["id"]),
                    )
                )
                instruction_membership_edges += 2

        for llvm_block in llvm_blocks:
            source = block_lookup[(function_id, llvm_block.name)]
            for successor_name in llvm_block.successors:
                target = block_lookup.get((function_id, successor_name))
                if target is None:
                    mapping_failures.append(
                        {
                            "function": function_id,
                            "symbol": symbol,
                            "source_block": llvm_block.name,
                            "missing_successor": successor_name,
                        }
                    )
                    continue
                links.append(_link(FLOW_BLOCK, source["id"], target["id"]))
                cfg_edges += 1

    # Cross-check the LLVM terminator CFG against ProGraML's instruction CFG.
    # This catches an order-based LLVM-name mapping that has the right block
    # count but assigns labels to the wrong ProGraML block IDs.
    node_by_id = {int(node["id"]): node for node in nodes}
    observed_cfg = {
        (
            (int(source.get("function", -1)), int(source.get("block", -1))),
            (int(target.get("function", -1)), int(target.get("block", -1))),
        )
        for link in links
        if int(link.get("flow", -1)) == FLOW_CONTROL
        for source, target in (
            (node_by_id[int(link["source"])], node_by_id[int(link["target"])]),
        )
        if int(source.get("type", -1)) == NODE_INSTRUCTION
        and int(target.get("type", -1)) == NODE_INSTRUCTION
        and (source.get("function"), source.get("block"))
        != (target.get("function"), target.get("block"))
    }
    expected_cfg = {
        (
            (function_id, int(source["block"])),
            (function_id, int(target["block"])),
        )
        for function_id, symbol in enumerate(function_names)
        for llvm_block in parsed.get(symbol, ())
        if (source := block_lookup.get((function_id, llvm_block.name))) is not None
        for successor in llvm_block.successors
        if (target := block_lookup.get((function_id, successor))) is not None
        if int(source["block"]) != int(target["block"])
    }
    missing = sorted(expected_cfg - observed_cfg)
    unexpected = sorted(observed_cfg - expected_cfg)
    if missing or unexpected:
        cfg_validation_failures.append(
            {
                "missing_from_programl_control": missing,
                "unexpected_programl_control": unexpected,
            }
        )

    stats = {
        "schema_version": HIERARCHY_SCHEMA_VERSION,
        "function_nodes_injected": len(function_lookup),
        "block_nodes_injected": len(block_lookup),
        "functions_mapped": mapped_functions,
        "instruction_membership_edges": instruction_membership_edges,
        "function_membership_edges": function_membership_edges,
        "cfg_edges": cfg_edges,
        "cfg_validation_failures": cfg_validation_failures,
        "mapping_failures": mapping_failures,
    }
    return block_lookup, function_lookup, stats


def _link(flow: int, source: int, target: int) -> dict:
    return {
        "flow": flow,
        "key": 0,
        "position": 0,
        "source": int(source),
        "target": int(target),
    }
