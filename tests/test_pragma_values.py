import tempfile
import unittest
from pathlib import Path

from hls_ir_graph.graph.pragma_values import (
    concrete_template_arguments,
    source_function,
    source_matches_instantiation,
)


class PragmaValueContextTests(unittest.TestCase):
    def test_extracts_template_parameters_and_local_constants(self):
        source = """\
template <class data_T, class res_T, typename CONFIG_T>
void normalize(hls::stream<data_T> &data) {
    constexpr unsigned multiplier_limit =
        DIV_ROUNDUP(data_T::size, CONFIG_T::reuse_factor);
    constexpr unsigned ii = data_T::size / multiplier_limit;
    #pragma HLS PIPELINE II=ii
}
"""
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "header.h"
            path.write_text(source)

            context = source_function(path, 7, "normalize")

        self.assertIsNotNone(context)
        self.assertEqual(
            context.parameter_names,
            ("data_T", "res_T", "CONFIG_T"),
        )
        self.assertTrue(context.uses_hls_stream)
        self.assertEqual(len(context.local_constants), 2)
        self.assertIn("multiplier_limit", context.local_constants[0])
        self.assertIn("ii =", context.local_constants[1])

    def test_extracts_nested_concrete_template_arguments(self):
        demangled = (
            "void nnet::normalize<"
            "nnet::array<ap_fixed<16, 6>, 8u>, "
            "nnet::array<ap_fixed<20, 10>, 8u>, config17"
            ">(hls::stream<int>&)"
        )

        arguments = concrete_template_arguments(demangled, "normalize")

        self.assertEqual(
            arguments,
            (
                "nnet::array<ap_fixed<16, 6>, 8u>",
                "nnet::array<ap_fixed<20, 10>, 8u>",
                "config17",
            ),
        )

    def test_distinguishes_stream_and_non_stream_overloads(self):
        context = type(
            "Context",
            (),
            {
                "parameter_names": ("data_T", "res_T", "CONFIG_T"),
                "uses_hls_stream": True,
            },
        )()
        stream = (
            "void nnet::normalize<int, int, config17>"
            "(hls::stream<int>&)"
        )
        parallel = "void nnet::normalize<int, int, config17>(int*)"

        self.assertTrue(
            source_matches_instantiation(context, stream, "normalize")
        )
        self.assertFalse(
            source_matches_instantiation(context, parallel, "normalize")
        )


if __name__ == "__main__":
    unittest.main()
