import unittest

from hls_ir_graph.graph.storage import compact_vitis_graph


class StorageCompactionTests(unittest.TestCase):
    def test_compact_mode_keeps_only_tensor_required_full_text(self):
        graph = {
            "nodes": [
                {
                    "type": 0,
                    "text": "add",
                    "features": {
                        "full_text": ["%x = add"],
                        "injector": ["llvm"],
                        "schema_version": ["3"],
                    },
                },
                {
                    "type": 0,
                    "text": "mul",
                    "features": {"full_text": ["%y = mul"], "llvm_loop_id": ["1"]},
                },
                {"type": 2, "text": "i32", "features": {"full_text": ["i32 7"]}},
                {
                    "type": 2,
                    "text": "[1024 x i32]",
                    "features": {"full_text": ["large aggregate"]},
                },
            ]
        }

        stats = compact_vitis_graph(graph, retain_full_text=False)

        self.assertNotIn("features", graph["nodes"][0])
        self.assertEqual(graph["nodes"][1]["features"], {"llvm_loop_id": ["1"]})
        self.assertEqual(graph["nodes"][2]["features"]["full_text"], ["i32 7"])
        self.assertNotIn("features", graph["nodes"][3])
        self.assertEqual(stats["full_text"], "scalar_constants_only")
        self.assertEqual(stats["full_text_fields_removed"], 3)
        self.assertEqual(stats["repeated_metadata_fields_removed"], 2)


if __name__ == "__main__":
    unittest.main()
