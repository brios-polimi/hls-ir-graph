from pathlib import Path
from tempfile import TemporaryDirectory

from hls_ir_graph.graph.hierarchy import add_llvm_hierarchy, parse_llvm_hierarchy


def test_cfg_uses_only_terminator_labels():
    with TemporaryDirectory() as directory:
        llvm = Path(directory) / "phi.ll"
        llvm.write_text(
            """define void @f(i1 %condition) {
entry:
  br i1 %condition, label %left, label %right
left:
  br label %join
right:
  br label %join
join:
  %value = phi i32 [ 1, %left ], [ 2, %right ]
  ret void
}
"""
        )

        blocks = parse_llvm_hierarchy(llvm)["f"]

        assert [(block.name, block.successors) for block in blocks] == [
            ("entry", ("left", "right")),
            ("left", ("join",)),
            ("right", ("join",)),
            ("join", ()),
        ]


def test_implicit_entry_block_is_retained():
    with TemporaryDirectory() as directory:
        llvm = Path(directory) / "implicit-entry.ll"
        llvm.write_text(
            """define i32 @f(i32 %value) {
  %incremented = add i32 %value, 1
  br label %done
done:
  ret i32 %incremented
}
"""
        )

        blocks = parse_llvm_hierarchy(llvm)["f"]

        assert [(block.name, block.successors) for block in blocks] == [
            ("entry", ("done",)),
            ("done", ()),
        ]


def test_single_block_self_loop_is_not_a_false_cfg_validation_failure():
    with TemporaryDirectory() as directory:
        llvm = Path(directory) / "loop.ll"
        llvm.write_text(
            """define void @f(i1 %again) {
loop:
  br i1 %again, label %loop, label %loop
}
"""
        )
        graph = {"graph": {"function": [{"name": "f"}]}}
        nodes = [
            {"id": 0, "type": 0, "function": 0, "block": 0, "text": "br"}
        ]
        links = []

        _, _, stats = add_llvm_hierarchy(graph, nodes, links, llvm, set())

        assert stats["cfg_edges"] == 1
        assert stats["cfg_validation_failures"] == []
        assert stats["schema_version"] == 3
        assert stats["membership_encoding"] == "node_fields"
        assert all(
            not (
                {nodes[edge["source"]]["type"], nodes[edge["target"]]["type"]}
                in ({0, 4}, {4, 5})
            )
            for edge in links
        )
