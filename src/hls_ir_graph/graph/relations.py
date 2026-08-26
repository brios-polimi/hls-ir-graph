"""Convert ProGraML flow-coded links to the canonical hierarchical schema."""

from __future__ import annotations

from collections import Counter


NODE_INSTRUCTION = 0
NODE_VARIABLE = 1
NODE_CONSTANT = 2
NODE_PRAGMA = 3
NODE_BLOCK = 4
NODE_FUNCTION = 5

FLOW_CONTROL = 0
FLOW_DATA = 1
FLOW_CALL = 2
FLOW_PRAGMA = 3
FLOW_BLOCK = 4
FLOW_FUNCTION = 5

RELATION_SCHEMA_VERSION = 1


def _relation(flow: int, source_type: int, target_type: int) -> str | None:
    if flow == FLOW_CONTROL and (source_type, target_type) == (
        NODE_INSTRUCTION,
        NODE_INSTRUCTION,
    ):
        return "control"
    if flow == FLOW_DATA:
        if source_type == NODE_INSTRUCTION and target_type == NODE_VARIABLE:
            return "defines"
        if source_type in (NODE_VARIABLE, NODE_CONSTANT) and target_type == NODE_INSTRUCTION:
            return "operand"
    if flow == FLOW_PRAGMA and source_type == NODE_PRAGMA:
        return "applies_to"
    if flow == FLOW_BLOCK:
        if source_type == NODE_BLOCK and target_type == NODE_BLOCK:
            return "control"
        if source_type == NODE_BLOCK and target_type == NODE_INSTRUCTION:
            return "contains"
        # instruction -> block is the redundant reverse membership edge.
        return None
    if flow == FLOW_FUNCTION:
        if source_type == NODE_FUNCTION and target_type == NODE_BLOCK:
            return "contains"
        # block -> function is the redundant reverse membership edge.
        return None
    return None


def canonicalize_relations(graph: dict) -> dict:
    """Replace overloaded numeric flows with endpoint-valid semantic relations.

    Calls become ``instruction -> function`` rather than ProGraML's pair of
    call/return edges between arbitrary entry, exit, and call instructions.
    Hierarchy membership is stored parent-to-child once; reverse relations can
    be derived by consumers that need them.
    """

    nodes = graph.get("nodes", [])
    links = graph.get("links", [])
    node_by_id = {int(node["id"]): node for node in nodes}
    function_nodes = {
        int(node.get("function", -1)): int(node["id"])
        for node in nodes
        if int(node.get("type", -1)) == NODE_FUNCTION
    }

    canonical: list[dict] = []
    seen: set[tuple[int, str, int, int]] = set()

    def add(source: int, relation: str, target: int, position: int = 0) -> None:
        key = (source, relation, target, position)
        if key in seen:
            return
        seen.add(key)
        canonical.append(
            {
                "source": source,
                "target": target,
                "relation": relation,
                "position": position,
            }
        )

    # Convert every non-call relation from its endpoint types.
    for link in links:
        flow = int(link.get("flow", -1))
        if flow == FLOW_CALL:
            continue
        source = int(link["source"])
        target = int(link["target"])
        source_type = int(node_by_id[source].get("type", -1))
        target_type = int(node_by_id[target].get("type", -1))
        relation = _relation(flow, source_type, target_type)
        if relation is not None:
            add(source, relation, target, int(link.get("position", 0)))

    # A forward ProGraML call edge starts at the callsite and targets the
    # callee entry. Return edges start at callee exits, so they fail this test.
    for link in links:
        if int(link.get("flow", -1)) != FLOW_CALL:
            continue
        source = int(link["source"])
        target = int(link["target"])
        source_node = node_by_id[source]
        if (
            int(source_node.get("type", -1)) != NODE_INSTRUCTION
            or source_node.get("text") not in {"call", "invoke", "callbr"}
        ):
            continue
        callee = function_nodes.get(int(node_by_id[target].get("function", -1)))
        if callee is not None:
            add(source, "calls", callee)

    # ProGraML's synthetic root/external instruction and declaration-only
    # placeholder are superseded by explicit function nodes.  The latter is
    # emitted as an instruction even though it has no basic block or edges.
    removed = {
        int(node["id"])
        for node in nodes
        if int(node.get("type", -1)) == NODE_INSTRUCTION
        and node.get("text") in {"[external]", "; undefined function"}
    }
    kept_nodes = [node for node in nodes if int(node["id"]) not in removed]
    id_map = {int(node["id"]): index for index, node in enumerate(kept_nodes)}
    for index, node in enumerate(kept_nodes):
        node["id"] = index
    graph["nodes"] = kept_nodes
    graph["links"] = [
        {
            **link,
            "source": id_map[int(link["source"])],
            "target": id_map[int(link["target"])],
        }
        for link in canonical
        if int(link["source"]) not in removed and int(link["target"]) not in removed
    ]
    final_counts = Counter(link["relation"] for link in graph["links"])
    stats = {
        "schema_version": RELATION_SCHEMA_VERSION,
        "relations": dict(final_counts),
        "synthetic_external_nodes_removed": len(removed),
    }
    graph["relation_schema"] = stats
    return stats
