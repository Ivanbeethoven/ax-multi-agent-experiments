"""Only the exact parser failure and observer-only command stream can close."""
import json
from pathlib import Path
import runpy
import tempfile
import unittest

REPO=Path(__file__).resolve().parents[1]
CODE=runpy.run_path(str(REPO/'experiments/unified-suite/pilot-aliyun/scripts/close-parser-before-work.py'))


class ParserClosure(unittest.TestCase):
    def test_command_ledger_rejects_any_mount_or_worker_action(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);observers=[dict(id='pool-b-'+r,directory='/srv/'+r+'/o/tag') for r in ('client','metadata','objects')]
            commands=[('client',['sha256sum','/srv/absolute'])]+[(r,['python3','/srv/code/make-observer.py','--role',r,'--out','/srv/'+r+'/q/tag-observer']) for r in ('client','metadata','objects')]
            for index,(role,argv) in enumerate(commands):
                folder=root/f'{index:04}';folder.mkdir();(folder/'intent.json').write_text(json.dumps(dict(role=role,argv=argv)));(folder/'exit.json').write_text('{"exit_code":0}')
            self.assertEqual(len(CODE['audit_commands'](root,observers)),4)
            (root/'0000/intent.json').write_text(json.dumps(dict(role='client',argv=['python3','/srv/pilot-node.py','start'])))
            with self.assertRaises(ValueError):CODE['audit_commands'](root,observers)

    def test_only_original_argument_rejection_without_work_outputs_is_supported(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);p=root/'stages/create-w01';p.mkdir(parents=True)
            (p/'finished.json').write_text('{"exit_code":2,"completed":true,"passed":false}')
            (p/'process.json').write_text('{"pid":999999999,"wrapper_pid":999999998}')
            (p/'stderr.log').write_text("argument --stage: invalid choice: 'create-w01'")
            stages=[dict(name='observe-start',status='complete'),dict(name='create-w01',status='failed')]
            self.assertEqual(CODE['parser_failure'](stages,root)['exit_code'],2)
            (root/'cohort').mkdir()
            with self.assertRaises(ValueError):CODE['parser_failure'](stages,root)
            (root/'cohort').rmdir();(p/'stderr.log').write_text('Actual runtime failure')
            with self.assertRaises(ValueError):CODE['parser_failure'](stages,root)


if __name__=='__main__':unittest.main()
