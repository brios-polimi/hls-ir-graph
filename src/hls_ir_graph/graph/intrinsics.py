"""Remove non-hardware LLVM markers and intrinsic declaration stubs."""

from __future__ import annotations

from collections import Counter, defaultdict
import re


NODE_INSTRUCTION = 0
NODE_BLOCK = 4
NODE_FUNCTION = 5
FLOW_CONTROL = 0
FLOW_BLOCK = 4

_NONSEMANTIC_CALL_RE = re.compile(
    r"@(?P<name>"
    r"llvm\.lifetime\.[-$._A-Za-z0-9]+|"
    r"llvm\.assume|"
    r"llvm\.dbg\.[-$._A-Za-z0-9]+|"
    r"llvm\.experimental\.noalias\.scope\.decl|"
    r"llvm\.sideeffect"
    r")\s*\("
)


def is_intrinsic_function_symbol(symbol: str) -> bool:
    """Return whether a declaration is an operator/marker, not a real function."""

    symbol = symbol.lstrip("@")
    return symbol.startswith("llvm.") or symbol.startswith("vitis.fpga.")


def is_compiler_initialization_function(symbol: str) -> bool:
    """Return whether ``symbol`` is C++ runtime startup code, not HLS hardware."""

    symbol = symbol.lstrip("@")
    return (
        symbol.startswith("__cxx_global_var_init")
        or symbol.startswith("__cxx_global_array_dtor")
        or symbol.startswith("_GLOBAL__sub_I_")
        or symbol.startswith("_GLOBAL__I_")
        or symbol.startswith("__static_initialization_and_destruction_0")
        or symbol.startswith("_Z41__static_initialization_and_destruction_0")
    )


def _full_text(node: dict) -> str:
    values = node.get("features", {}).get("full_text", [])
    return "\n".join(map(str, values)) if isinstance(values, list) else str(values)


def prune_nonsemantic_intrinsics(graph: dict) -> dict:
    """Prune marker calls, bypass control edges, and remove intrinsic stubs.

    Value-producing intrinsic calls such as ``vitis.fpga.*part.select`` remain
    ordinary instruction nodes. Only their external declaration placeholder is
    removed. ``llvm.sideeffect`` is removed here after pragma recovery.
    """

    nodes = graph.get("nodes", [])
    links = graph.get("links", [])
    function_symbols = [
        str(record.get("name", "")).lstrip("@")
        for record in graph.get("graph", {}).get("function", [])
    ]
    intrinsic_function_ids = {
        index
        for index, symbol in enumerate(function_symbols)
        if is_intrinsic_function_symbol(symbol)
    }
    initialization_function_ids = {
        index
        for index, symbol in enumerate(function_symbols)
        if is_compiler_initialization_function(symbol)
    }
    node_by_id = {int(node["id"]): node for node in nodes}
    callees: dict[int, set[int]] = defaultdict(set)
    for link in links:
        if int(link.get("flow", -1)) != 2:
            continue
        source = node_by_id[int(link["source"])]
        target = node_by_id[int(link["target"])]
        if (
            int(source.get("type", -1)) == NODE_INSTRUCTION
            and source.get("text") in {"call", "invoke", "callbr"}
        ):
            callees[int(source.get("function", -1))].add(
                int(target.get("function", -1))
            )

    def closure(starts: set[int]) -> set[int]:
        reached = set(starts)
        frontier = list(starts)
        while frontier:
            for callee in callees[frontier.pop()]:
                if callee >= 0 and callee not in reached:
                    reached.add(callee)
                    frontier.append(callee)
        return reached

    initialization_reachable = closure(initialization_function_ids)
    ordinary_functions = set(range(len(function_symbols))) - initialization_reachable
    ordinary_reachable = closure(ordinary_functions)
    compiler_only_function_ids = initialization_reachable - ordinary_reachable

    removed_reasons: dict[int, str] = {}
    for node in nodes:
        node_id = int(node["id"])
        function_id = int(node.get("function", -1))
        if function_id in compiler_only_function_ids:
            removed_reasons[node_id] = "cxx_runtime_initialization"
            continue
        match = _NONSEMANTIC_CALL_RE.search(_full_text(node))
        if int(node.get("type", -1)) == NODE_INSTRUCTION and match:
            removed_reasons[node_id] = match.group("name")
            continue
        if (
            function_id in intrinsic_function_ids
            and node.get("text") in {"[external]", "llvm.function"}
        ):
            removed_reasons[node_id] = "intrinsic_declaration_stub"

    removed = set(removed_reasons)
    if not removed:
        return {
            "nodes_removed": 0,
            "function_nodes_removed": 0,
            "block_nodes_removed": 0,
            "control_bypass_edges_added": 0,
            "block_cfg_edges_removed": 0,
            "removed_by_kind": {},
        }

    removed_block_links = [
        link
        for link in links
        if int(link.get("flow", -1)) == FLOW_BLOCK
        and (int(link["source"]) in removed or int(link["target"]) in removed)
    ]
    block_cfg_edges_removed = sum(
        int(node_by_id[int(link["source"])].get("type", -1)) == NODE_BLOCK
        and int(node_by_id[int(link["target"])].get("type", -1)) == NODE_BLOCK
        for link in removed_block_links
    )
    function_nodes_removed = sum(
        int(node.get("type", -1)) == NODE_FUNCTION
        and int(node["id"]) in removed
        for node in nodes
    )
    block_nodes_removed = sum(
        int(node.get("type", -1)) == NODE_BLOCK
        and int(node["id"]) in removed
        for node in nodes
    )

    outgoing_control: dict[int, list[dict]] = defaultdict(list)
    incoming_control: dict[int, list[dict]] = defaultdict(list)
    for link in links:
        if int(link.get("flow", -1)) != FLOW_CONTROL:
            continue
        outgoing_control[int(link["source"])].append(link)
        incoming_control[int(link["target"])].append(link)

    bypass_pairs: set[tuple[int, int, int]] = set()
    for start in removed:
        predecessors = {
            int(link["source"])
            for link in incoming_control.get(start, ())
            if int(link["source"]) not in removed
        }
        if not predecessors:
            continue
        frontier = [start]
        visited: set[int] = set()
        while frontier:
            current = frontier.pop()
            if current in visited:
                continue
            visited.add(current)
            for link in outgoing_control.get(current, ()):
                target = int(link["target"])
                if target in removed:
                    frontier.append(target)
                    continue
                position = int(link.get("position", 0))
                bypass_pairs.update((source, target, position) for source in predecessors)

    kept_nodes = [node for node in nodes if int(node["id"]) not in removed]
    id_map = {int(node["id"]): index for index, node in enumerate(kept_nodes)}
    for index, node in enumerate(kept_nodes):
        node["id"] = index

    kept_links = [
        {
            **link,
            "source": id_map[int(link["source"])],
            "target": id_map[int(link["target"])],
        }
        for link in links
        if int(link["source"]) not in removed and int(link["target"]) not in removed
    ]
    existing = {
        (int(link["flow"]), int(link["source"]), int(link["target"]), int(link.get("position", 0)))
        for link in kept_links
        if "flow" in link
    }
    bypass_added = 0
    for source, target, position in sorted(bypass_pairs):
        mapped = (FLOW_CONTROL, id_map[source], id_map[target], position)
        if mapped in existing:
            continue
        kept_links.append(
            {
                "flow": FLOW_CONTROL,
                "key": 0,
                "position": position,
                "source": id_map[source],
                "target": id_map[target],
            }
        )
        existing.add(mapped)
        bypass_added += 1

    graph["nodes"] = kept_nodes
    graph["links"] = kept_links
    return {
        "nodes_removed": len(removed),
        "function_nodes_removed": function_nodes_removed,
        "block_nodes_removed": block_nodes_removed,
        "control_bypass_edges_added": bypass_added,
        "block_cfg_edges_removed": block_cfg_edges_removed,
        "removed_by_kind": dict(Counter(removed_reasons.values())),
    }
