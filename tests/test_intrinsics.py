from hls_ir_graph.graph.intrinsics import prune_nonsemantic_intrinsics


def test_marker_pruning_bypasses_control_and_keeps_value_intrinsics():
    graph = {
        "graph": {
            "function": [
                {"name": "kernel"},
                {"name": "llvm.lifetime.start.p0i8"},
                {"name": "vitis.fpga.legacy.part.select.i8"},
            ]
        },
        "nodes": [
            {"id": 0, "type": 0, "function": 0, "text": "alloca"},
            {
                "id": 1,
                "type": 0,
                "function": 0,
                "text": "call",
                "features": {"full_text": ["call void @llvm.lifetime.start.p0i8()"]},
            },
            {
                "id": 2,
                "type": 0,
                "function": 0,
                "text": "call",
                "features": {
                    "full_text": [
                        "%x = call i8 @vitis.fpga.legacy.part.select.i8(i8 %v, i32 7, i32 7)"
                    ]
                },
            },
            {"id": 3, "type": 0, "function": 0, "text": "ret"},
        ],
        "links": [
            {"flow": 0, "source": 0, "target": 1, "position": 0},
            {"flow": 0, "source": 1, "target": 2, "position": 0},
            {"flow": 0, "source": 2, "target": 3, "position": 0},
        ],
    }

    stats = prune_nonsemantic_intrinsics(graph)

    assert stats["nodes_removed"] == 1
    assert any(
        "@vitis.fpga." in node.get("features", {}).get("full_text", [""])[0]
        for node in graph["nodes"]
    )
    assert any(
        edge["flow"] == 0 and edge["source"] == 0 and edge["target"] == 1
        for edge in graph["links"]
    )


def test_cxx_runtime_initializer_function_is_removed_whole():
    graph = {
        "graph": {
            "function": [
                {"name": "kernel"},
                {"name": "__cxx_global_var_init.1"},
            ]
        },
        "nodes": [
            {"id": 0, "type": 0, "function": 0, "text": "ret"},
            {"id": 1, "type": 5, "function": 0, "text": "llvm.function"},
            {"id": 2, "type": 0, "function": 1, "text": "call"},
            {"id": 3, "type": 4, "function": 1, "text": "llvm.basic_block"},
            {"id": 4, "type": 5, "function": 1, "text": "llvm.function"},
            {"id": 5, "type": 3, "function": 1, "text": "pragma.inline"},
        ],
        "links": [
            {"flow": 4, "source": 3, "target": 2, "position": 0},
            {"flow": 5, "source": 4, "target": 3, "position": 0},
            {"flow": 3, "source": 5, "target": 2, "position": 0},
        ],
    }

    stats = prune_nonsemantic_intrinsics(graph)

    assert stats["function_nodes_removed"] == 1
    assert stats["block_nodes_removed"] == 1
    assert stats["removed_by_kind"]["cxx_runtime_initialization"] == 4
    assert {node["function"] for node in graph["nodes"]} == {0}


def test_initializer_only_callee_is_removed_but_shared_callee_is_kept():
    graph = {
        "graph": {
            "function": [
                {"name": "kernel"},
                {"name": "__cxx_global_var_init"},
                {"name": "constructor_only"},
                {"name": "shared_helper"},
            ]
        },
        "nodes": [
            {"id": 0, "type": 0, "function": 0, "text": "call"},
            {"id": 1, "type": 0, "function": 1, "text": "call"},
            {"id": 2, "type": 0, "function": 1, "text": "call"},
            {"id": 3, "type": 0, "function": 2, "text": "ret"},
            {"id": 4, "type": 0, "function": 3, "text": "ret"},
            {"id": 5, "type": 5, "function": 0, "text": "llvm.function"},
            {"id": 6, "type": 5, "function": 1, "text": "llvm.function"},
            {"id": 7, "type": 5, "function": 2, "text": "llvm.function"},
            {"id": 8, "type": 5, "function": 3, "text": "llvm.function"},
        ],
        "links": [
            {"flow": 2, "source": 0, "target": 4, "position": 0},
            {"flow": 2, "source": 1, "target": 3, "position": 0},
            {"flow": 2, "source": 2, "target": 4, "position": 0},
        ],
    }

    prune_nonsemantic_intrinsics(graph)

    kept_functions = {node["function"] for node in graph["nodes"]}
    assert kept_functions == {0, 3}
