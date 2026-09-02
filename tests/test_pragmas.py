import json
import tempfile
import unittest
from pathlib import Path

from hls_ir_graph.graph.pragmas import (
    inject_vitis_pragmas,
    parse_pragma_options,
)


class PragmaOptionTests(unittest.TestCase):
    def test_parses_assignments_flags_targets_and_quoted_values(self):
        parsed = parse_pragma_options(
            'variable = weights type=cyclic factor=4 dim=1 rewind bundle="memory bus"'
        )

        self.assertEqual(parsed["variable"], ("weights",))
        self.assertEqual(parsed["type"], ("cyclic",))
        self.assertEqual(parsed["factor"], ("4",))
        self.assertEqual(parsed["dim"], ("1",))
        self.assertEqual(parsed["rewind"], ("true",))
        self.assertEqual(parsed["bundle"], ("memory bus",))


class PragmaInjectionTests(unittest.TestCase):
    def test_labelled_loop_pragmas_attach_to_named_llvm_block_nodes(self):
        block_names = ["entry", "ReuseLoop", "for.cond", "MultLoop", "inner.cond"]
        graph = {
            "graph": {"function": [{"name": "kernel"}]},
            "nodes": [
                {
                    "id": index,
                    "type": 0,
                    "text": "br" if index < len(block_names) - 1 else "ret",
                    "function": 0,
                    "block": 10 + index,
                    "features": {"full_text": [f"instruction {index}"]},
                }
                for index in range(len(block_names))
            ],
            "links": [],
        }
        source = """\
void kernel() {
    #pragma HLS DATAFLOW
ReuseLoop:
    for (int i = 0; i < 4; ++i) {
        #pragma HLS PIPELINE II=1
    MultLoop:
        for (int j = 0; j < 2; ++j) {
            #pragma HLS UNROLL
        }
    }
}
"""
        llvm = """\
define void @kernel() {
entry:
  br label %ReuseLoop
ReuseLoop:
  br label %for.cond
for.cond:
  br label %MultLoop
MultLoop:
  br label %inner.cond
inner.cond:
  ret void
}
"""

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_path = root / "source.cpp"
            llvm_path = root / "kernel.ll"
            graph_path = root / "graph.json"
            dump_path = root / "pragmas.log"
            source_path.write_text(source)
            llvm_path.write_text(llvm)
            graph_path.write_text(json.dumps(graph))
            dump_path.write_text(
                f"{source_path}:5:1: warning: HLS pragma dump "
                "PragmaType=PIPELINE_XLX_SEP_ PragmaFunction=kernel_XLX_SEP_ "
                "PragmaOptions=II=1 [-Wdump-hls-pragmas]\n"
                f"{source_path}:8:1: warning: HLS pragma dump "
                "PragmaType=UNROLL_XLX_SEP_ PragmaFunction=kernel_XLX_SEP_ "
                "PragmaOptions= [-Wdump-hls-pragmas]\n"
                f"{source_path}:2:1: warning: HLS pragma dump "
                "PragmaType=DATAFLOW_XLX_SEP_ PragmaFunction=kernel_XLX_SEP_ "
                "PragmaOptions= [-Wdump-hls-pragmas]\n"
            )

            stats = inject_vitis_pragmas(
                graph_path,
                dump_path,
                llvm_path=llvm_path,
            )
            injected = json.loads(graph_path.read_text())

            block_nodes = {
                node["features"]["name"][0]: node
                for node in injected["nodes"]
                if node["type"] == 4
            }
            self.assertEqual(set(block_nodes), set(block_names))
            self.assertEqual(stats["block_nodes_injected"], len(block_names))
            self.assertEqual(stats["loop_scope_nodes"], 2)

            pragmas = {
                node["text"]: node
                for node in injected["nodes"]
                if node["type"] == 3
            }
            for directive, block_name in (
                ("pragma.pipeline", "ReuseLoop"),
                ("pragma.unroll", "MultLoop"),
            ):
                pragma = pragmas[directive]
                targets = [
                    link["target"]
                    for link in injected["links"]
                    if link["relation"] == "applies_to"
                    and link["source"] == pragma["id"]
                ]
                self.assertEqual(targets, [block_nodes[block_name]["id"]])
                self.assertEqual(
                    pragma["features"]["anchor_reason"],
                    ["source_loop_label"],
                )
                self.assertEqual(
                    pragma["features"]["attachment_confidence"],
                    ["exact"],
                )
                self.assertEqual(
                    pragma["features"]["attachment_schema_version"],
                    ["4"],
                )
                self.assertEqual(
                    pragma["features"]["source_loop_label"],
                    [block_name],
                )

            dataflow = pragmas["pragma.dataflow"]
            dataflow_targets = [
                link["target"]
                for link in injected["links"]
                if link["relation"] == "applies_to"
                and link["source"] == dataflow["id"]
            ]
            function_node = next(
                node for node in injected["nodes"] if node["type"] == 5
            )
            self.assertEqual(dataflow_targets, [function_node["id"]])
            self.assertEqual(
                dataflow["features"]["anchor_reason"],
                ["function_scope"],
            )

            reuse = block_nodes["ReuseLoop"]
            reuse_members = {
                node["id"]
                for node in injected["nodes"]
                if node["type"] == 0
                and node["function"] == reuse["function"]
                and node["block"] == reuse["block"]
            }
            reuse_successors = {
                link["target"]
                for link in injected["links"]
                if link["source"] == reuse["id"]
                and link["relation"] == "control"
            }
            self.assertIn(1, reuse_members)
            self.assertIn(block_nodes["for.cond"]["id"], reuse_successors)

            # Block and pragma enrichment remains idempotent.
            inject_vitis_pragmas(
                graph_path,
                dump_path,
                llvm_path=llvm_path,
            )
            reinjected = json.loads(graph_path.read_text())
            self.assertEqual(
                sum(node["type"] == 4 for node in reinjected["nodes"]),
                len(block_names),
            )
            self.assertEqual(
                sum(node["type"] == 3 for node in reinjected["nodes"]),
                3,
            )

    def test_dump_semantics_are_merged_with_carrier_anchors(self):
        graph = {
            "graph": {"function": [{"name": "kernel"}]},
            "nodes": [
                {
                    "id": 0,
                    "type": 0,
                    "text": "call",
                    "function": 0,
                    "block": 0,
                    "features": {
                        "full_text": [
                            'call void @llvm.sideeffect() [ "xlx_array_partition"(%weights, i32 2) ]'
                        ]
                    },
                },
                {
                    "id": 1,
                    "type": 1,
                    "text": "i32*",
                    "function": 0,
                    "block": 0,
                    "features": {"full_text": ["%weights = alloca i32*"]},
                },
            ],
            "links": [
                {"source": 1, "target": 0, "flow": 1, "position": 0}
            ],
        }
        dump = (
            "/tmp/source.cpp:12:3: warning: HLS pragma dump "
            "PragmaType=ARRAY_PARTITION_XLX_SEP_ "
            "PragmaFunction=kernel_XLX_SEP_ "
            "PragmaOptions=variable=weights type=cyclic factor=4 dim=1 "
            "[-Wdump-hls-pragmas]\n"
            "#pragma HLS ARRAY_PARTITION variable=weights complete\n"
        )

        with tempfile.TemporaryDirectory() as temp:
            graph_path = Path(temp) / "graph.json"
            dump_path = Path(temp) / "pragmas.log"
            graph_path.write_text(json.dumps(graph))
            dump_path.write_text(dump)

            expected_label = {"lut": 12.0, "ff": 34.0}
            stats = inject_vitis_pragmas(
                graph_path, dump_path, label=expected_label
            )
            injected = json.loads(graph_path.read_text())
            pragma_nodes = [
                node for node in injected["nodes"] if node["type"] == 3
            ]

            self.assertEqual(len(pragma_nodes), 1)
            self.assertEqual(injected["labels"], expected_label)
            self.assertEqual(stats["carrier_pragmas_matched"], 1)
            self.assertEqual(stats["carrier_pragmas_injected"], 0)
            features = pragma_nodes[0]["features"]
            self.assertEqual(features["schema_version"], ["2"])
            self.assertEqual(
                json.loads(features["arguments_json"][0]),
                {
                    "_carrier_arg_1": ["2"],
                    "dim": ["1"],
                    "factor": ["4"],
                    "type": ["cyclic"],
                    "variable": ["weights"],
                },
            )
            self.assertEqual(features["anchor_reason"], ["carrier_exact"])
            self.assertEqual(features["attachment_confidence"], ["exact"])
            self.assertEqual(features["full_text"], [dump.splitlines()[0]])
            self.assertEqual(
                features["pragma_text"],
                ["#pragma HLS ARRAY_PARTITION variable=weights complete"],
            )
            pragma_id = pragma_nodes[0]["id"]
            pragma_targets = [
                injected["nodes"][link["target"]]
                for link in injected["links"]
                if link["relation"] == "applies_to"
                and link["source"] == pragma_id
            ]
            self.assertEqual(len(pragma_targets), 1)
            self.assertEqual(pragma_targets[0]["type"], 1)

            # Reinjection replaces, rather than duplicates, generated nodes.
            inject_vitis_pragmas(graph_path, dump_path)
            reinjected = json.loads(graph_path.read_text())
            self.assertEqual(
                sum(node["type"] == 3 for node in reinjected["nodes"]), 1
            )

    def test_variable_identity_fans_out_only_to_same_object_occurrences(self):
        graph = {
            "graph": {"function": [{"name": "kernel"}]},
            "nodes": [
                {
                    "id": 0,
                    "type": 0,
                    "text": "entry",
                    "function": 0,
                    "block": 0,
                    "features": {"full_text": ["define void @kernel()"]},
                },
                {
                    "id": 1,
                    "type": 1,
                    "text": "i32*",
                    "function": 0,
                    "block": 0,
                    "features": {"full_text": ["%weights = alloca i32"]},
                },
                {
                    "id": 2,
                    "type": 1,
                    "text": "i32*",
                    "function": 0,
                    "block": 1,
                    "features": {"full_text": ["%weights.addr = phi i32*"]},
                },
                {
                    "id": 3,
                    "type": 0,
                    "text": "load",
                    "function": 0,
                    "block": 1,
                    "features": {"full_text": ["%v = load i32, i32* %weights"]},
                },
            ],
            "links": [],
        }
        dump = (
            "/tmp/source.cpp:2:1: warning: HLS pragma dump "
            "PragmaType=INTERFACE_XLX_SEP_ PragmaFunction=kernel_XLX_SEP_ "
            "PragmaOptions=port=weights mode=m_axi [-Wdump-hls-pragmas]\n"
        )

        injected = self._inject(graph, dump)
        pragma = next(node for node in injected["nodes"] if node["type"] == 3)
        targets = {
            link["target"]
            for link in injected["links"]
            if link["source"] == pragma["id"]
            and link["relation"] == "applies_to"
        }
        self.assertEqual(targets, {1, 2})
        self.assertEqual(
            pragma["features"]["anchor_reason"], ["variable_identity"]
        )

    def test_stream_dump_reconciles_with_pipe_depth_carrier(self):
        graph = {
            "graph": {"function": [{"name": "kernel"}]},
            "nodes": [
                {
                    "id": 0,
                    "type": 0,
                    "text": "call",
                    "function": 0,
                    "block": 0,
                    "features": {
                        "full_text": [
                            'call void @llvm.sideeffect() [ "xlx_reqd_pipe_depth"(%fifo, i32 4) ]'
                        ]
                    },
                },
                {
                    "id": 1,
                    "type": 1,
                    "text": "ptr",
                    "function": 0,
                    "block": 0,
                    "features": {"full_text": ["%fifo = alloca i8"]},
                },
            ],
            "links": [],
        }
        dump = (
            "/tmp/source.cpp:3:1: warning: HLS pragma dump "
            "PragmaType=STREAM_XLX_SEP_ PragmaFunction=kernel_XLX_SEP_ "
            "PragmaOptions=variable=fifo depth=4 [-Wdump-hls-pragmas]\n"
        )

        injected = self._inject(graph, dump)
        pragmas = [node for node in injected["nodes"] if node["type"] == 3]
        self.assertEqual(len(pragmas), 1)
        self.assertEqual(pragmas[0]["text"], "pragma.stream")
        self.assertEqual(
            pragmas[0]["features"]["anchor_reason"], ["carrier_exact"]
        )

    def test_global_carrier_fans_out_to_all_exact_constant_occurrences(self):
        symbol = "_ZZ3foovE1x"
        graph = {
            "graph": {"function": [{"name": "_Z3foov"}]},
            "nodes": [
                {
                    "id": 0,
                    "type": 0,
                    "text": "call",
                    "function": 0,
                    "block": 0,
                    "features": {
                        "full_text": [
                            f'call void @llvm.sideeffect() [ "xlx_array_partition"(@{symbol}, i32 2) ]'
                        ]
                    },
                },
                {
                    "id": 1,
                    "type": 2,
                    "text": "ptr",
                    "function": 0,
                    "block": 0,
                    "features": {"full_text": [f"@{symbol} = internal global"]},
                },
                {
                    "id": 2,
                    "type": 2,
                    "text": "ptr",
                    "function": 0,
                    "block": 1,
                    "features": {"full_text": [f"i32* @{symbol}"]},
                },
            ],
            "links": [],
        }
        dump = (
            "/tmp/source.cpp:4:1: warning: HLS pragma dump "
            "PragmaType=ARRAY_PARTITION_XLX_SEP_ PragmaFunction=foo_XLX_SEP_ "
            "PragmaOptions=variable=x factor=2 [-Wdump-hls-pragmas]\n"
        )

        injected = self._inject(graph, dump)
        pragma = next(node for node in injected["nodes"] if node["type"] == 3)
        targets = [
            injected["nodes"][link["target"]]
            for link in injected["links"]
            if link["source"] == pragma["id"]
            and link["relation"] == "applies_to"
        ]
        self.assertEqual(len(targets), 2)
        self.assertTrue(all(target["type"] == 2 for target in targets))
        self.assertEqual(
            pragma["features"]["resolved_global_symbols"], [symbol]
        )

    def test_function_matching_does_not_use_substrings(self):
        graph = {
            "graph": {"function": [{"name": "_ZN4nnet4reluEv"}]},
            "nodes": [
                {
                    "id": 0,
                    "type": 0,
                    "text": "entry",
                    "function": 0,
                    "block": 0,
                    "features": {"full_text": ["entry"]},
                }
            ],
            "links": [],
        }
        dump = (
            "/tmp/source.cpp:1:1: warning: HLS pragma dump "
            "PragmaType=PIPELINE_XLX_SEP_ PragmaFunction=elu_XLX_SEP_ "
            "PragmaOptions=II=1 [-Wdump-hls-pragmas]\n"
        )

        injected = self._inject(graph, dump)
        self.assertFalse(any(node["type"] == 3 for node in injected["nodes"]))
        self.assertEqual(
            injected["pragma_injection"]["unmatched_records"][0]["function"],
            "elu",
        )

    def test_unresolved_variable_does_not_fall_back_to_function_entry(self):
        graph = {
            "graph": {"function": [{"name": "kernel"}]},
            "nodes": [
                {
                    "id": 0,
                    "type": 0,
                    "text": "entry",
                    "function": 0,
                    "block": 0,
                    "features": {"full_text": ["entry"]},
                }
            ],
            "links": [],
        }
        dump = (
            "/tmp/source.cpp:1:1: warning: HLS pragma dump "
            "PragmaType=INTERFACE_XLX_SEP_ PragmaFunction=kernel_XLX_SEP_ "
            "PragmaOptions=port=missing mode=m_axi [-Wdump-hls-pragmas]\n"
        )

        injected = self._inject(graph, dump)
        self.assertFalse(any(node["type"] == 3 for node in injected["nodes"]))

    def _inject(self, graph, dump):
        with tempfile.TemporaryDirectory() as temp:
            graph_path = Path(temp) / "graph.json"
            dump_path = Path(temp) / "pragmas.log"
            graph_path.write_text(json.dumps(graph))
            dump_path.write_text(dump)
            inject_vitis_pragmas(graph_path, dump_path)
            return json.loads(graph_path.read_text())

    def test_unmatched_dump_record_is_retained_as_audit_metadata(self):
        graph = {"nodes": [], "links": []}
        dump = (
            "/tmp/source.cpp:1:1: warning: HLS pragma dump "
            "PragmaType=DATAFLOW_XLX_SEP_ PragmaFunction=missing_XLX_SEP_ "
            "PragmaOptions=disable_start_propagation [-Wdump-hls-pragmas]\n"
        )

        with tempfile.TemporaryDirectory() as temp:
            graph_path = Path(temp) / "graph.json"
            dump_path = Path(temp) / "pragmas.log"
            graph_path.write_text(json.dumps(graph))
            dump_path.write_text(dump)

            stats = inject_vitis_pragmas(graph_path, dump_path)
            injected = json.loads(graph_path.read_text())

            self.assertEqual(stats["pragmas_unmatched"], 1)
            self.assertEqual(injected["nodes"], [])
            self.assertEqual(injected["links"], [])
            unmatched = injected["pragma_injection"]["unmatched_records"]
            self.assertEqual(len(unmatched), 1)
            self.assertEqual(unmatched[0]["directive"], "dataflow")
            self.assertEqual(
                unmatched[0]["arguments"],
                {"disable_start_propagation": ["true"]},
            )


if __name__ == "__main__":
    unittest.main()
