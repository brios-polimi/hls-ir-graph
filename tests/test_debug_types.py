import shutil
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from hls_ir_graph.frontends.debug_types import recover_debug_types
from hls_ir_graph.pipeline import preprocess_project


class DebugTypeTests(unittest.TestCase):
    def test_identical_layouts_keep_distinct_semantics_and_exact_tokens(self):
        ir = '''%class.ac_fixed = type { i16 }
%class.ac_fixed.10 = type { i16 }
; do not rewrite %class.ac_fixed in comments
@text = constant [15 x i8] c"%class.ac_fixed\\00"
define void @f(%class.ac_fixed* %a, %class.ac_fixed.10* %b) {
 call void @llvm.dbg.value(metadata %class.ac_fixed* %a, metadata !1, metadata !DIExpression())
 call void @llvm.dbg.value(metadata %class.ac_fixed.10* %b, metadata !2, metadata !DIExpression())
 ret void
}
!1 = !DILocalVariable(name: "a", type: !3)
!2 = !DILocalVariable(name: "b", type: !4)
!3 = !DIDerivedType(tag: DW_TAG_pointer_type, baseType: !5)
!4 = !DIDerivedType(tag: DW_TAG_pointer_type, baseType: !6)
!5 = !DICompositeType(tag: DW_TAG_class_type, name: "ac_fixed<16, 6, true, (ac_q_mode)0, (ac_o_mode)0>")
!6 = !DICompositeType(tag: DW_TAG_class_type, name: "ac_fixed<16, 1, true, (ac_q_mode)6, (ac_o_mode)1>")
'''
        result, table = recover_debug_types(ir)
        self.assertTrue(table['complete_ac_mapping'])
        self.assertEqual(table['mapped_ac_type_count'], 2)
        self.assertIn('%"ac_fixed<16, 6, true, (ac_q_mode)0, (ac_o_mode)0>" = type { i16 }', result)
        self.assertIn('%"ac_fixed<16, 1, true, (ac_q_mode)6, (ac_o_mode)1>" = type { i16 }', result)
        self.assertIn('; do not rewrite %class.ac_fixed in comments', result)
        self.assertIn('c"%class.ac_fixed\\00"', result)

    def test_conflicting_associations_are_not_guessed(self):
        ir = '''%class.ac_int = type { i16 }
call void @llvm.dbg.value(metadata %class.ac_int* %a, metadata !1, metadata !DIExpression())
call void @llvm.dbg.value(metadata %class.ac_int* %b, metadata !2, metadata !DIExpression())
!1 = !DILocalVariable(type: !3)
!2 = !DILocalVariable(type: !4)
!3 = !DICompositeType(name: "ac_int<16, true>")
!4 = !DICompositeType(name: "ac_int<16, false>")
'''
        result, table = recover_debug_types(ir)
        self.assertEqual(result, ir)
        self.assertFalse(table['complete_ac_mapping'])
        self.assertEqual(table['conflicts'], ['%class.ac_int'])

    def test_missing_debug_or_fragments_cannot_claim_complete_coverage(self):
        for expression in ('!DIExpression(DW_OP_LLVM_fragment, 0, 16)', '!DIExpression(DW_OP_plus_uconst, 2)'):
            ir = f'''%class.ac_int = type {{ i16 }}
call void @llvm.dbg.value(metadata %class.ac_int* %a, metadata !1, metadata {expression})
!1 = !DILocalVariable(type: !2)
!2 = !DICompositeType(name: "ac_int<16, true>")
'''
            result, table = recover_debug_types(ir)
            self.assertEqual(result, ir)
            self.assertFalse(table['complete_ac_mapping'])
        _, table = recover_debug_types('%class.ac_int = type { i16 }\n')
        self.assertFalse(table['complete_ac_mapping'])

    def test_global_constant_uses_explicit_single_storage_ancestry(self):
        ir = '''%"class.ac_private::iv_base.7" = type { i9, [6 x i8] }
@pixels = constant [3 x { %"class.ac_private::iv_base.7" }] zeroinitializer, !dbg !1
!1 = !DIGlobalVariableExpression(var: !2, expr: !DIExpression())
!2 = !DIGlobalVariable(name: "pixels", type: !3)
!3 = !DICompositeType(tag: DW_TAG_array_type, baseType: !4)
!4 = !DIDerivedType(tag: DW_TAG_const_type, baseType: !5)
!5 = !DICompositeType(name: "ac_int<9, false>", size: 64, elements: !6)
!6 = !{!7}
!7 = !DIDerivedType(tag: DW_TAG_inheritance, baseType: !8)
!8 = !DICompositeType(name: "iv_base<1, false, 9, false>", scope: !9, size: 64)
!9 = !DINamespace(name: "ac_private")
'''
        result, table = recover_debug_types(ir)
        self.assertTrue(table['complete_ac_mapping'])
        self.assertIn('%"ac_private::iv_base<1, false, 9, false>"', result)
        _, table = recover_debug_types(ir.replace('@pixels', '@global'))
        self.assertTrue(table['complete_ac_mapping'])
        for invalid in (ir.replace('baseType: !8)', 'baseType: !8, offset: 8)'),
                        ir.replace('size: 64, elements', 'size: 128, elements')):
            _, table = recover_debug_types(invalid)
            self.assertFalse(table['complete_ac_mapping'])

    def test_debug_spelling_collisions_preserve_llvm_type_identity(self):
        ir = '''%class.ac_int = type { i16 }
%class.ac_int.1 = type { i16 }
call void @llvm.dbg.value(metadata %class.ac_int* %a, metadata !1, metadata !DIExpression())
call void @llvm.dbg.value(metadata %class.ac_int.1* %b, metadata !1, metadata !DIExpression())
!1 = !DILocalVariable(type: !2)
!2 = !DICompositeType(name: "ac_int<16, true>")
'''
        _, table = recover_debug_types(ir)
        names = [r['emitted_name'] for r in table['llvm_types']]
        self.assertEqual(len(set(names)), 2)
        self.assertTrue(table['complete_ac_mapping'])

    @unittest.skipUnless(shutil.which('clang-16') and shutil.which('llvm-as-16'), 'LLVM 16 tools required')
    def test_real_clang_module_remains_valid_after_recovery(self):
        source = '''template<int W, bool S> struct ac_int { int value; };
ac_int<9, false> global_values[2];
void f(ac_int<16, true>* out) { ac_int<16, true> local; local.value=7; *out=local; }
'''
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cpp, ll = root/'types.cpp', root/'types.ll'
            cpp.write_text(source)
            subprocess.run(['clang-16', str(cpp), '-S', '-emit-llvm', '-g', '-fstandalone-debug',
                            '-O0', '-Xclang', '-disable-llvm-passes', '-Xclang', '-no-opaque-pointers',
                            '-o', str(ll)], check=True, capture_output=True)
            result, table = recover_debug_types(ll.read_text())
            self.assertTrue(table['complete_ac_mapping'])
            self.assertEqual(table['mapped_ac_type_count'], 2)
            ll.write_text(result)
            subprocess.run(['llvm-as-16', str(ll), '-o', str(root/'types.bc')], check=True, capture_output=True)

    @unittest.skipUnless(shutil.which('clang-16'), 'Clang 16 required')
    def test_frontend_emits_inspectable_table_before_debug_stripping(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root/'project'
            (project/'firmware/ac_types').mkdir(parents=True)
            (project/'firmware/myproject.cpp').write_text(
                'template<int W, bool S> struct ac_int { int value; };\n'
                'void myproject(ac_int<9, false>* out) { out->value=3; }\n'
            )
            artifacts = preprocess_project(project, root/'output', backend='bambu', graph=False)
            table = json.loads(artifacts.type_table.read_text())
            self.assertTrue(table['complete_ac_mapping'])
            self.assertEqual(table['ac_type_count'], 1)
            self.assertIn('!DICompositeType', artifacts.debug_llvm.read_text())
            self.assertNotIn('!DICompositeType', artifacts.llvm.read_text())
            self.assertNotIn('optnone', artifacts.llvm.read_text())
            self.assertIn('%"ac_int<9, false>"', artifacts.llvm.read_text())
            provenance = json.loads(artifacts.provenance.read_text())
            self.assertEqual(provenance['type_table'], str(artifacts.type_table))


if __name__ == '__main__':
    unittest.main()
