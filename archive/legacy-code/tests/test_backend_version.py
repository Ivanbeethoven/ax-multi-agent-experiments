"""Branch-following updates and image provenance, without external services."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('resolve_backend', ROOT/'infra/rustfs/resolve_backend.py')
backend = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backend)


class VersionTests(unittest.TestCase):
    def test_latest_requires_exact_branch_and_full_revision(self):
        with patch.object(backend, 'command', return_value='a'*40+'\trefs/heads/'+backend.BRANCH) as call:
            self.assertEqual(backend.latest_commit(), 'a'*40)
            self.assertEqual(call.call_args.args[0][-1], 'refs/heads/'+backend.BRANCH)
        for reply in ('', 'a'*40+'\trefs/heads/main', 'abcd\trefs/heads/'+backend.BRANCH,
                      'a'*40+'\trefs/heads/'+backend.BRANCH+'\n'+'b'*40+'\trefs/heads/main'):
            with patch.object(backend, 'command', return_value=reply), self.assertRaises(ValueError):
                backend.latest_commit()

    def test_source_fast_forwards_but_preserves_local_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            seed, checkout = Path(tmp)/'seed', Path(tmp)/'checkout'
            def git(path, *args):
                return subprocess.check_output(['git','-C',str(path),*args],text=True,stderr=subprocess.PIPE).strip()
            seed.mkdir(); git(seed,'init','-b',backend.BRANCH)
            git(seed,'config','user.name','Fixture'); git(seed,'config','user.email','fixture@example.invalid')
            def commit(path, text):
                (path/'file').write_text(text); git(path,'add','file'); git(path,'commit','-m',text)
                return git(path,'rev-parse','HEAD')
            first=commit(seed,'first')
            git(seed,'clone','--branch',backend.BRANCH,str(seed),str(checkout))
            latest=commit(seed,'second')
            with patch.object(backend,'REPOSITORY',str(seed)):
                receipt=backend.update_source(checkout)
                self.assertEqual(receipt,{'source_commit':latest,'previous_commit':first,'updated':True})
                (checkout/'file').write_text('local edit')
                with self.assertRaisesRegex(ValueError,'local changes'):backend.update_source(checkout)
                self.assertEqual((checkout/'file').read_text(),'local edit')
                (checkout/'file').write_text('second')
                git(checkout,'config','user.name','Fixture');git(checkout,'config','user.email','fixture@example.invalid')
                local=commit(checkout,'local commit');commit(seed,'upstream change')
                with self.assertRaises(subprocess.CalledProcessError):backend.update_source(checkout)
                self.assertEqual(git(checkout,'rev-parse','HEAD'),local)
                self.assertEqual((checkout/'file').read_text(),'local commit')

    def test_image_must_match_current_branch_and_records_actual_revision(self):
        revision='a'*40; reference='registry/rustfs@sha256:'+'b'*64
        image={'Id':'sha256:built','Config':{'Labels':{'org.opencontainers.image.revision':revision}},
               'RepoDigests':[reference]}
        with tempfile.TemporaryDirectory() as tmp:
            binary=Path(tmp)/'brewfs';binary.write_bytes(b'binary fixture')
            with patch.object(backend,'latest_commit',return_value=revision), \
                 patch.object(backend,'clean_commit',side_effect=[revision,'c'*40]), \
                 patch.object(backend,'command',return_value=json.dumps([image])):
                result=backend.verify_image('rustfs','brewfs',binary,reference)
            self.assertEqual(result['source_commit'],revision)
            self.assertNotIn('frozen_commit',result)
            self.assertFalse(result['runtime_verified'])
            self.assertEqual(result['brewfs_binary_sha256'],hashlib.sha256(binary.read_bytes()).hexdigest())
            with patch.object(backend,'latest_commit',return_value='d'*40), \
                 patch.object(backend,'clean_commit',return_value=revision), patch.object(backend,'command') as call:
                with self.assertRaisesRegex(ValueError,'branch advanced'):
                    backend.verify_image('rustfs','brewfs',binary,reference)
                call.assert_not_called()
            image['Config']['Labels']['org.opencontainers.image.revision']='d'*40
            with patch.object(backend,'latest_commit',return_value=revision), \
                 patch.object(backend,'clean_commit',return_value=revision), \
                 patch.object(backend,'command',return_value=json.dumps([image])):
                with self.assertRaisesRegex(ValueError,'not built from the latest'):
                    backend.verify_image('rustfs','brewfs',binary,reference)

    def test_receipt_records_policy_and_retains_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)/'version.json'
            args=['source','--rustfs-source',tmp,'--output',str(output)]
            with patch.object(backend,'update_source',side_effect=ValueError('local changes')):
                self.assertEqual(backend.main(args),1)
            receipt=json.loads(output.read_text())
            self.assertEqual(receipt['revision_policy'],'branch-latest')
            self.assertEqual(receipt['blocked_reason'],'local changes')
            with patch.object(backend,'update_source') as call, self.assertRaisesRegex(ValueError,'new version receipt'):
                backend.main(args)
            call.assert_not_called()


if __name__ == '__main__':unittest.main()
