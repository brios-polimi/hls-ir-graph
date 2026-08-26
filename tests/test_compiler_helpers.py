import tempfile
import unittest
from pathlib import Path

from hls_ir_graph.frontends.common import (
    _collapse_static_initializers,
    _failure_detail,
    _remove_generated_weight_initializers,
    _strip_nonsemantic_metadata,
)
from hls_ir_graph.frontends.vitis import _remove_unsupported_resource_pragmas


class CompilerHelperTests(unittest.TestCase):
    def test_failure_detail_prefers_error_over_trailing_warnings(self):
        output = (
            "source.cpp:10:2: error: no template named 'Missing'\n"
            "source.cpp:20:2: warning: pragma dump one\n"
            "source.cpp:30:2: warning: pragma dump two\n"
        )
        detail = _failure_detail(output)
        self.assertIn("error: no template named 'Missing'", detail)
        self.assertNotIn("pragma dump", detail)

    def test_removes_only_unsupported_resource_pragma(self):
        source = (
            "#pragma HLS PIPELINE II=1\n"
            "#pragma HLS RESOURCE variable=w core=ROM_nP_BRAM\n"
            "void f() {}\n"
        )
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "source.cpp"
            path.write_text(source)
            self.assertEqual(_remove_unsupported_resource_pragmas(path), 1)
            cleaned = path.read_text()
            self.assertIn("#pragma HLS PIPELINE", cleaned)
            self.assertNotIn("ROM_nP_BRAM", cleaned)
            self.assertEqual(cleaned.count("\n"), source.count("\n"))
            self.assertEqual(cleaned.splitlines()[2], "void f() {}")

    def test_collapses_only_global_initializer_body(self):
        ir = (
            "define internal void @__cxx_global_var_init() {\n"
            "entry:\n  call void @expensive()\n  ret void\n}\n"
            "define void @kernel() {\nentry:\n  ret void\n}\n"
        )
        collapsed = _collapse_static_initializers(ir)
        self.assertNotIn("@expensive", collapsed)
        self.assertIn("define void @kernel()", collapsed)

    def test_removes_only_generated_weight_header_values(self):
        source = (
            '# 1 "/project/firmware/weights/w2.h" 1\n'
            "weight2_t w2[3] = {1.0, 2.0, 3.0};\n"
            "scale_t s2[2] = {{1.0, -3}, {1.0, -2}};\n"
            '# 1 "/project/firmware/myproject.cpp" 2\n'
            "int lookup[2] = {4, 5};\n"
        )
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "source.pp.cpp"
            path.write_text(source)
            self.assertEqual(_remove_generated_weight_initializers(path), 2)
            cleaned = path.read_text()
        self.assertIn("weight2_t w2[3];", cleaned)
        self.assertIn("scale_t s2[2];", cleaned)
        self.assertIn("int lookup[2] = {4, 5};", cleaned)

    def test_strips_metadata_but_keeps_instruction(self):
        ir = "  %1 = add i32 %a, %b, !dbg !12\n!12 = !{}\n"
        self.assertEqual(
            _strip_nonsemantic_metadata(ir),
            "  %1 = add i32 %a, %b\n",
        )


if __name__ == "__main__":
    unittest.main()
