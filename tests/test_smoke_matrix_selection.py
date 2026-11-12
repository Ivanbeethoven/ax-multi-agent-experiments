import json
import io
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

COMMON = ['--brewfs','/base','--producer','/producer','--smallfiles','/smallfiles',
          '--juicefs','/juicefs','--brewfs-revision','a'*40,'--rustfs-image','sha256:'+'b'*64,
          '--rustfs-revision','c'*40,'--brewfs-provenance','/build.json',
          '--trace','/trace','--replay','/replay','--full-trace','--tiny',
          '--checkpoint','/checkpoint.json','--ssh-config','/ssh.conf','--target-host','target',
          '--target-repo','/repo','--target-tools','/tools']


class MatrixSelection(unittest.TestCase):
    def test_selected_retry_keeps_other_cases_untouched_and_uses_candidate(self):
        from bench.experiment_smoke_matrix import main
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); overrides=root/'overrides.json'
            overrides.write_text(json.dumps({'C4-fork-brewfs-full':['--brewfs','/candidate']}))
            calls=[]
            def run(command,**_):
                calls.append(command)
                output=Path(command[command.index('--out')+1]);output.mkdir()
                (output/'receipt.json').write_text('{"status":"passed"}')
                return subprocess.CompletedProcess(command,0)
            argv=['matrix','--out',str(root/'out'),'--case-overrides',str(overrides),
                  '--only','C4-fork-brewfs-full',*COMMON]
            with patch.object(sys,'argv',argv),patch('bench.experiment_smoke_matrix.subprocess.run',side_effect=run):
                self.assertEqual(main(),0)
            self.assertEqual(len(calls),1)
            self.assertEqual([calls[0][i+1] for i,x in enumerate(calls[0]) if x=='--brewfs'],['/base','/candidate'])
            self.assertFalse((root/'out/C4-fork-juicefs').exists())

    def test_blocks_cover_all_modes_and_keep_controls_in_their_block(self):
        from bench.experiment_blocks import BLOCKS, cases, selected_cases
        from bench.experiment_modes import MODES
        all_cases={case for name in MODES for case in cases(name)}
        self.assertEqual(set(selected_cases(BLOCKS)),all_cases)
        self.assertEqual(len(all_cases),26)
        self.assertEqual(BLOCKS['c4-fork']['cases'],['C4-fork-brewfs-full','C4-fork-juicefs'])
        self.assertIn('C2-replay-brewfs-full',BLOCKS['c2-ablations']['cases'])
        self.assertEqual(len(BLOCKS['c4-handoff']['cases']),4)

    def test_dry_run_does_not_create_output_or_execute_and_uses_shared_config(self):
        from bench.experiment_smoke_matrix import main
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);common=root/'common.json';common.write_text(json.dumps(COMMON))
            stream=io.StringIO()
            argv=['matrix','--block','c4-handoff','--common-args',str(common),
                  '--out',str(root/'out'),'--dry-run']
            with patch.object(sys,'argv',argv),patch('bench.experiment_smoke_matrix.subprocess.run') as run,redirect_stdout(stream):
                self.assertEqual(main(),0)
            plan=json.loads(stream.getvalue())
            self.assertEqual(len(plan['commands']),4)
            self.assertFalse(plan['formal_sample'])
            self.assertFalse(plan['runtime_qualification_verified'])
            self.assertFalse((root/'out').exists())
            run.assert_not_called()

    def test_invalid_arguments_fail_before_output_or_process(self):
        from bench.experiment_smoke_matrix import main
        for extra in (['--only','unknown'], ['--block','c4-fork','--placement=move'],
                      ['--block','c1-io','--only','C1-io-juicefs']):
            with self.subTest(extra=extra),tempfile.TemporaryDirectory() as folder:
                out=Path(folder)/'out'
                with patch.object(sys,'argv',['matrix','--out',str(out),*COMMON,*extra]),redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit):main()
                self.assertFalse(out.exists())
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder)/'out'
            missing=COMMON.copy();index=missing.index('--trace');del missing[index:index+2]
            with patch.object(sys,'argv',['matrix','--out',str(out),'--block','c2-trace',*missing]),redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):main()
            self.assertFalse(out.exists())

    def test_nonzero_or_missing_receipt_stops_block_and_summary_remains_readable(self):
        from bench.experiment_smoke_matrix import main
        import runpy
        root_dir=Path(__file__).resolve().parents[1]
        summarize=runpy.run_path(str(root_dir/'experiments/unified-suite/pilot-aliyun/scripts/summarize-smoke-check.py'))['summarize']
        for value in ({'status':'passed'},None,{'status':'unknown'},[]):
            with self.subTest(value=value),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);calls=[]
                def run(command,**_):
                    calls.append(command)
                    output=Path(command[command.index('--out')+1]);output.mkdir()
                    if value is not None:(output/'receipt.json').write_text(json.dumps(value))
                    return subprocess.CompletedProcess(command,1)
                argv=['matrix','--out',str(root/'out'),'--block','c1-io',*COMMON]
                with patch.object(sys,'argv',argv),patch('bench.experiment_smoke_matrix.subprocess.run',side_effect=run),redirect_stdout(io.StringIO()):
                    self.assertEqual(main(),2)
                self.assertEqual(len(calls),1)
                rows=json.loads((root/'out/summary.json').read_text())
                self.assertEqual(rows[0]['status'],'failed')
                self.assertFalse((root/'out/C1-io-juicefs').exists())
                report=summarize(root/'out')
                self.assertEqual(report['counts'],{'failed':1})

    def test_all_handoff_variants_dispatch_sequentially_with_no_agent_argument(self):
        from bench.experiment_smoke_matrix import main
        from bench.experiment_blocks import BLOCKS
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder)/'out';calls=[]
            def run(command,**_):
                calls.append(command)
                case=Path(command[command.index('--out')+1]);case.mkdir()
                (case/'receipt.json').write_text('{"status":"passed"}')
                return subprocess.CompletedProcess(command,0)
            with patch.object(sys,'argv',['matrix','--out',str(out),'--block','c4-handoff',*COMMON]),patch('bench.experiment_smoke_matrix.subprocess.run',side_effect=run),redirect_stdout(io.StringIO()):
                self.assertEqual(main(),0)
            self.assertEqual([x['name'] for x in json.loads((out/'summary.json').read_text())],BLOCKS['c4-handoff']['cases'])
            self.assertTrue(all('--opencode' not in cmd for cmd in calls))
