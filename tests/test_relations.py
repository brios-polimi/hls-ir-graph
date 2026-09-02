from hls_ir_graph.graph.relations import canonicalize_relations


def _node(node_id, node_type, text="", *, function=0, block=0, pragma=0):
    return {
        "id": node_id,
        "type": node_type,
        "text": text,
        "function": function,
        "block": block,
        "pragma": pragma,
    }


def test_canonical_relations_are_directional_and_endpoint_specific():
    graph = {
        "nodes": [
            _node(0, 0, "call", function=0, block=0),
            _node(1, 0, "entry", function=1, block=0),
            _node(2, 1, "argument", function=0, block=0),
            _node(3, 3, "pragma.pipeline", function=0, block=0),
            _node(4, 4, "entry", function=0, block=0),
            _node(5, 4, "entry", function=1, block=0),
            _node(6, 5, "caller", function=0),
            _node(7, 5, "callee", function=1),
            _node(8, 0, "[external]", function=-1, block=-1),
            _node(9, 0, "; undefined function", function=2, block=0),
        ],
        "links": [
            {"source": 0, "target": 1, "flow": 2, "position": 0},
            {"source": 1, "target": 0, "flow": 2, "position": 0},
            {"source": 2, "target": 0, "flow": 1, "position": 1},
            {"source": 0, "target": 2, "flow": 1, "position": 0},
            {"source": 3, "target": 2, "flow": 3, "position": 0},
            {"source": 4, "target": 0, "flow": 4, "position": 0},
            {"source": 0, "target": 4, "flow": 4, "position": 0},
            {"source": 6, "target": 4, "flow": 5, "position": 0},
            {"source": 4, "target": 6, "flow": 5, "position": 0},
            {"source": 8, "target": 0, "flow": 0, "position": 0},
        ],
    }

    canonicalize_relations(graph)

    assert all("flow" not in edge for edge in graph["links"])
    assert all(node["text"] != "[external]" for node in graph["nodes"])
    assert all(node["text"] != "; undefined function" for node in graph["nodes"])
    triples = {
        (
            graph["nodes"][edge["source"]]["text"],
            edge["relation"],
            graph["nodes"][edge["target"]]["text"],
        )
        for edge in graph["links"]
    }
    assert triples == {
        ("argument", "operand", "call"),
        ("call", "defines", "argument"),
        ("pragma.pipeline", "applies_to", "argument"),
        ("call", "calls", "callee"),
    }
    assert all(edge.get("position") != 0 for edge in graph["links"])
