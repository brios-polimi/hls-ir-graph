import tempfile
import unittest
import json
from pathlib import Path
from hls_ir_graph.config import PreprocessConfig

from hls_ir_graph.frontends.bambu import (
    _bambu_args,
    extract_source_directives,
)
from hls_ir_graph.frontends.vitis import vitis_args


class ProjectPreprocessorTests(unittest.TestCase):
    def test_extracts_active_pragmas_with_source_locations(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source.pp.cpp"
            source.write_text(
                '# 7 "/tmp/project/firmware/myproject.cpp"\n'
                '#pragma HLS PIPELINE II=2\n'
                'int x;\n'
                '# 31 "/tmp/project/firmware/nnet_utils/nnet_dense.h" 2\n'
                '#pragma HLS ARRAY_PARTITION variable=weights complete\n'
                '#pragma HLS_interface mode=valid port=data\n'
            )
            records = extract_source_directives(source, backend="bambu")

        self.assertEqual(len(records), 3)
        self.assertEqual(records[0].directive, "pipeline")
        self.assertEqual(records[0].source_line, 7)
        self.assertEqual(records[1].source_line, 31)
        self.assertEqual(records[2].directive, "interface")

    def test_bambu_args_require_project_owned_ac_types(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            with self.assertRaises(FileNotFoundError):
                _bambu_args(project)
            (project / "firmware/ac_types").mkdir(parents=True)
            args = _bambu_args(project)

        self.assertIn("-D__SYNTHESIS__", args)
        self.assertIn("-D__BAMBU__", args)
        self.assertNotIn("-D__NO_INLINE__", args)  # Clang defines this at -O0.
        self.assertTrue(any(arg.endswith("firmware/ac_types") for arg in args))

    def test_vitis_uses_vendor_hls_types_and_retains_static_guard_policy(self):
        args = vitis_args(Path('/project'), PreprocessConfig())
        self.assertIn('-fhls', args)
        self.assertIn('-fno-threadsafe-statics', args)
        self.assertFalse(any('/firmware/ap_types' in a for a in args))
        self.assertNotIn('-D__SYNTHESIS__', args)  # Provided by -fhls.

    def test_removed_bambu_options_have_an_explicit_migration_error(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'config.json'
            path.write_text(json.dumps({'bambu': {'architecture_xml': 'old.xml'}}))
            with self.assertRaisesRegex(ValueError, 'debug-derived type recovery'):
                PreprocessConfig.from_file(path)


if __name__ == "__main__":
    unittest.main()
