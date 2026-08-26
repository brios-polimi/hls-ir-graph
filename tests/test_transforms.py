import tempfile
import unittest
from pathlib import Path

from hls_ir_graph.transforms import IrTransformContext, apply_transforms, register


@register("test_append_marker")
def _append_marker(ir: str, *, context: IrTransformContext, marker: str) -> str:
    return ir + f"; {context.backend}:{marker}\n"


class IrTransformTests(unittest.TestCase):
    def test_applies_ordered_configured_transform_and_records_options(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            llvm = root / "kernel.ll"
            llvm.write_text("; module\n")
            context = IrTransformContext("bambu", root, root / "source.cpp", root / "directives.log")
            applied = apply_transforms(
                llvm,
                [{"name": "test_append_marker", "options": {"marker": "augmented"}}],
                context,
            )
            self.assertIn("bambu:augmented", llvm.read_text())
            self.assertEqual(applied[0]["name"], "test_append_marker")


if __name__ == "__main__":
    unittest.main()
