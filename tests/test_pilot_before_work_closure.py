"""A before-work closure must reject any command that could have started work."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

FILE=Path(__file__).resolve().parents[1]/'experiments/unified-suite/pilot-aliyun/scripts/close-before-work.py'
spec=importlib.util.spec_from_file_location('before_work_closure',FILE)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


class BeforeWorkAudit(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.tool=Path('/srv/ax-client/code-12/experiments/unified-suite/pilot-aliyun/scripts/make-observer.py')
        self.target='/srv/ax-client/q/old-tag-observer'
        self.command('0000',['sha256sum','/srv/ax-client/bin/brewfs'],0)
        self.command('0001',['python3',str(self.tool),'--out',self.target],1,
                     "a.out.mkdir(parents=True,exist_ok=False)\nFileExistsError: [Errno 17] File exists: '"+self.target+"'\n")
    def tearDown(self):self.tmp.cleanup()
    def command(self,name,argv,exit_,error=''):
        path=self.root/name;path.mkdir(exist_ok=True)
        (path/'intent.json').write_text(json.dumps(dict(role='client',argv=argv)))
        (path/'exit.json').write_text(json.dumps(dict(exit_code=exit_)))
        (path/'stderr').write_text(error)
    def test_readonly_checks_and_first_client_mkdir_collision_pass(self):
        self.assertEqual(len(module.audit_commands(self.root,self.tool,self.target)),2)
    def test_prior_mount_or_worker_command_is_never_treated_as_not_started(self):
        for argv in (['python3','node.py','start'],['touch','/srv/ax-client/q/new'],['sha256sum','relative']):
            self.command('0000',argv,0)
            with self.assertRaisesRegex(ValueError,'may have created'):module.audit_commands(self.root,self.tool,self.target)
    def test_success_or_another_generator_failure_cannot_use_this_closure(self):
        for code,error in ((0,'FileExistsError'),(1,'Network timeout'),(2,'Argument error')):
            self.command('0001',['python3',str(self.tool),'--out',self.target],code,error)
            with self.assertRaisesRegex(ValueError,'first-client directory collision'):module.audit_commands(self.root,self.tool,self.target)
    def test_commands_after_the_collision_are_rejected(self):
        self.command('0002',['sha256sum','/srv/ax-client/bin/brewfs'],0)
        with self.assertRaises(ValueError):module.audit_commands(self.root,self.tool,self.target)
    def test_missing_command_ledger_entry_is_rejected(self):
        self.command('0003',['sha256sum','/srv/ax-client/bin/brewfs'],0)
        with self.assertRaisesRegex(ValueError,'contiguous'):module.audit_commands(self.root,self.tool,self.target)


if __name__=='__main__':unittest.main()
