"""Reject unsupported replay policies before a real filesystem is allocated."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

REPO=Path(__file__).resolve().parents[1]
SCRIPT=REPO/'experiments/unified-suite/pilot-aliyun/scripts/prepare-c2.py'
spec=importlib.util.spec_from_file_location('prepare_c2_capability',SCRIPT)
prepare=importlib.util.module_from_spec(spec);spec.loader.exec_module(prepare)


class ReplayCapability(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.binary=self.root/'replay';self.binary.write_bytes(b'exact frozen replay')

    def tearDown(self):self.tmp.cleanup()

    def test_parser_failure_preserves_raw_evidence_and_cannot_pass(self):
        out=self.root/'preflight'
        def rejected(argv,stdout,stderr):
            stderr.write(b'unknown/duplicate option or missing think plan\n')
            return SimpleNamespace(returncode=1)
        with patch.object(prepare.subprocess,'run',side_effect=rejected):
            with self.assertRaisesRegex(ValueError,'CLI capability check failed'):
                prepare.preflight_replay(self.binary,out,portable_terminal_probe=True)
        self.assertEqual(prepare.load(out/'exit.json')['exit_code'],1)
        self.assertIn('--portable-terminal-probe',prepare.load(out/'command.json')['argv'])
        self.assertIn('unknown/duplicate',(out/'stderr').read_text())
        self.assertFalse((out/'verification.json').exists())
        self.assertEqual(len((out/'events.jsonl').read_text().splitlines()),5)

    def test_zero_exit_without_exact_read_and_policy_evidence_is_rejected(self):
        def incomplete(argv,stdout,stderr):
            Path(argv[4]).write_text(json.dumps(dict(valid=True,completed=True)))
            return SimpleNamespace(returncode=0)
        with patch.object(prepare.subprocess,'run',side_effect=incomplete):
            with self.assertRaisesRegex(ValueError,'capability report'):
                prepare.preflight_replay(self.binary,self.root/'preflight',cache_parent_fds=True)

    def test_capability_failure_precedes_main_asset_output_and_mount_commands(self):
        case=self.root/'pytest-full-02';case.mkdir()
        plan=self.root/'plan.json';plan.write_text(json.dumps(dict(cells=[dict(
            id='native',workload='trace',arm_id='native',params=dict(trace=case.name))])))
        asset=self.root/'asset';out=self.root/'qualification'
        argv=['prepare-c2.py','--product','brewfs','--asset',str(asset),'--out',str(out),
              '--plan',str(plan),'--case',str(case),'--database','53',
              '--node-tool',str(self.binary),'--qualifier',str(self.binary),
              '--node-tool-sha256',prepare.digest(self.binary),'--qualifier-sha256',prepare.digest(self.binary)]
        with patch.object(prepare.sys,'argv',argv),patch.object(prepare,'preflight_case',return_value={}), \
                patch.object(prepare,'preflight_replay',side_effect=ValueError('unsupported flag')) as probe, \
                patch.object(prepare.subprocess,'Popen') as start:
            with self.assertRaisesRegex(ValueError,'unsupported flag'):prepare.main()
        probe.assert_called_once();start.assert_not_called()
        self.assertFalse(asset.exists());self.assertFalse(out.exists())

    @unittest.skipUnless(os.name=='posix' and os.environ.get('FULL_FUSE_REPLAY_BIN'),
                         'requires an actual Linux Rust replay executable')
    def test_actual_replay_binary_with_the_requested_route(self):
        cache=os.environ.get('AX_REPLAY_PARENT_FDS')=='1'
        portable=os.environ.get('AX_REPLAY_PORTABLE','1')=='1'
        result=prepare.preflight_replay(Path(os.environ['FULL_FUSE_REPLAY_BIN']),self.root/'actual',portable,cache)
        self.assertTrue(result['passed'])
        report=prepare.load(self.root/'actual/replay.json')
        self.assertEqual(report['actual_read_bytes'],5)
        self.assertEqual(report['path_resolution'],'parent-fd-cache' if cache else 'full-path')


if __name__=='__main__':unittest.main()
