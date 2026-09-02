import shutil
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

    @unittest.skipUnless(shutil.which("opt-16"), "LLVM opt-16 is required")
    def test_llvm_opt_promotes_stack_values_and_restores_target(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            llvm = root / "kernel.ll"
            llvm.write_text(
                'target triple = "fpga64-xilinx-none"\n\n'
                "define i32 @kernel(i32 %input) {\n"
                "entry:\n"
                "  %slot = alloca i32\n"
                "  store i32 %input, i32* %slot\n"
                "  %value = load i32, i32* %slot\n"
                "  ret i32 %value\n"
                "}\n"
            )
            context = IrTransformContext(
                "vitis", root, root / "source.cpp", root / "directives.log"
            )
            applied = apply_transforms(
                llvm,
                [{
                    "name": "llvm_opt",
                    "options": {
                        "passes": "sroa,mem2reg",
                        "binary": shutil.which("opt-16"),
                    },
                }],
                context,
            )
            result = llvm.read_text()
            self.assertNotIn("alloca", result)
            self.assertIn('target triple = "fpga64-xilinx-none"', result)
            self.assertEqual(applied[0]["name"], "llvm_opt")


if __name__ == "__main__":
    unittest.main()
