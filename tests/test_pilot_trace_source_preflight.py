"""Reject a damaged source before allocating an asset; restore only a new copy."""
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO/'experiments/unified-suite/pilot-aliyun/scripts'


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value); return value


restore = module('restore_trace_input', SCRIPTS/'restore-trace-input.py')
prepare = module('prepare_trace_source', SCRIPTS/'prepare-c2.py')


@unittest.skipUnless(os.name=='posix', 'Actual Linux cp, hardlinks and Unix modes')
class TraceSource(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.case = self.root/'original/pytest-full-02'
        initial = self.case/'initial'; (initial/'repo').mkdir(parents=True)
        (initial/'state/home').mkdir(parents=True)
        (initial/'repo/a').write_bytes(b'original data')
        os.link(initial/'repo/a', initial/'repo/a-alias')
        (initial/'repo/link').symlink_to('a')
        (initial/'repo').chmod(0o777); (initial/'repo/a').chmod(0o777)
        self.expected = restore.tree(initial)
        (initial/'repo').chmod(0o755); (initial/'repo/a').chmod(0o755)
        self.before = restore.tree(initial)
        (self.case/'capture/blobs').mkdir(parents=True)
        (self.case/'capture/events.jsonl').write_bytes(b'{"op":"read"}\n')
        (self.case/'capture/blobs/blob').write_bytes(b'payload')
        for name, value in [('metrics.json', dict(callbacks=1)), ('initial-tree.json', self.expected),
                            ('final-tree.json', {}), ('hardlink-check.json', dict(groups=[]))]:
            (self.case/name).write_text(json.dumps(value))
        inventory = {p.relative_to(self.case).as_posix():restore.digest(p)
                     for p in self.case.rglob('*') if p.is_file() and not p.is_symlink()}
        self.manifest = self.root/'manifest.json'; self.manifest.write_text(json.dumps(inventory))
        self.args = SimpleNamespace(source_case=self.case, destination_case=self.root/'restored/cases/pytest-full-02',
            manifest=self.manifest, manifest_sha256=restore.digest(self.manifest), expected_files=len(inventory), out=self.root/'proof')

    def tearDown(self): self.tmp.cleanup()

    def test_complete_inventory_modes_and_hardlinks_without_changing_source(self):
        with patch('builtins.print'): restore.restore(self.args)
        self.assertEqual(restore.tree(self.args.destination_case/'initial'), self.expected)
        self.assertEqual(restore.tree(self.case/'initial'), self.before)
        self.assertEqual(restore.hardlinks(self.args.destination_case/'initial'), [['repo/a', 'repo/a-alias']])
        self.assertTrue((self.args.destination_case/'initial/repo/link').is_symlink())
        result = restore.load(self.args.out/'result.json')
        self.assertTrue(result['source_unchanged']); self.assertEqual(result['source_mode_differences'], 3)
        self.assertEqual(result['regular_files'], self.args.expected_files)
        self.assertTrue(all(restore.load(p)['exit_code']==0 for p in self.args.out.glob('copy-*/exit.json')))

    def test_source_payload_corruption_cannot_be_repaired_by_chmod(self):
        (self.case/'initial/repo/a').write_bytes(b'changed bytes')
        with self.assertRaisesRegex(ValueError, 'payload differs'): restore.restore(self.args)
        self.assertFalse(self.args.destination_case.exists())
        self.assertFalse(restore.load(self.args.out/'failed.json')['passed'])

    def test_existing_destination_and_wrong_case_name_are_rejected(self):
        self.args.destination_case.mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, 'new case'): restore.restore(self.args)
        self.assertFalse(self.args.out.exists())
        self.args.destination_case = self.root/'other-name'
        with self.assertRaisesRegex(ValueError, 'same name'): restore.restore(self.args)

    def test_escape_link_is_rejected_and_external_inode_is_never_changed(self):
        outside = self.root/'outside'; outside.write_bytes(b'outside'); outside.chmod(0o600)
        (self.case/'initial/repo/escape').symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'link escapes'): restore.restore(self.args)
        self.assertEqual(stat.S_IMODE(outside.stat().st_mode), 0o600)
        self.assertFalse(self.args.destination_case.exists())

    def test_original_manifest_and_full_file_count_are_required(self):
        self.args.expected_files += 1
        with self.assertRaisesRegex(ValueError, 'Complete declared'): restore.restore(self.args)
        self.assertFalse(self.args.destination_case.exists())

    def test_valid_source_preflight_requires_modes_as_well_as_bytes(self):
        with self.assertRaisesRegex(ValueError, 'Initial source tree differs'): prepare.preflight_case(self.case)
        for name, value in self.expected.items():
            if value['type']!='link': (self.case/'initial'/name).chmod(value['mode'])
        checked=prepare.preflight_case(self.case)
        self.assertTrue(checked['passed']); self.assertEqual(checked['initial_hardlink_groups'], [['repo/a','repo/a-alias']])

    def test_unaccounted_source_hardlink_alias_refuses_preflight(self):
        for name,value in self.expected.items():
            if value['type']!='link':(self.case/'initial'/name).chmod(value['mode'])
        os.link(self.case/'initial/repo/a',self.root/'external-alias')
        with self.assertRaisesRegex(ValueError,'hardlink group leaves'):prepare.preflight_case(self.case)

    def test_standalone_copy_imports_the_exact_node_tool_runtime(self):
        standalone=self.root/'standalone-preparation.py';standalone.write_bytes((SCRIPTS/'prepare-c2.py').read_bytes())
        result=subprocess.run([prepare.sys.executable,str(standalone),'--node-tool',str(SCRIPTS/'pilot-node.py'),'--help'],
                              capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('--case',result.stdout)

    def test_main_source_failure_precedes_output_asset_and_commands(self):
        plan = self.root/'plan.json'
        plan.write_text(json.dumps(dict(cells=[dict(id='native',workload='trace',arm_id='native',params=dict(trace='pytest-full-02'))])))
        tool = self.root/'node.py'; tool.write_text('pinned test helper')
        argv = ['prepare-c2.py','--product','brewfs','--asset',str(self.root/'asset'),'--out',str(self.root/'qualification'),
            '--plan',str(plan),'--case',str(self.case),'--node-tool',str(tool),'--node-tool-sha256',restore.digest(tool),
            '--qualifier',str(tool),'--qualifier-sha256',restore.digest(tool),'--database','53']
        with patch.object(prepare.sys, 'argv', argv), patch.object(prepare.subprocess, 'Popen') as run:
            with self.assertRaisesRegex(ValueError, 'Initial source tree differs'): prepare.main()
        run.assert_not_called()
        self.assertFalse((self.root/'asset').exists()); self.assertFalse((self.root/'qualification').exists())


if __name__=='__main__': unittest.main()
