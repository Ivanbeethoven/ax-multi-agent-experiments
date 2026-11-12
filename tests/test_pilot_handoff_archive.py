"""Run the real readback bootstrap with a stand-in for the SSH transport."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from bench import pilot_handoff_archive as archive
from bench import pilot_handoff_closure_bridge as bridge


@unittest.skipUnless(sys.platform.startswith('linux'),'target readback requires Linux file descriptors')
class TestHandoffArchive(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name).resolve()
        self.handoff=self.root/'attempt/handoff'
        self.close=self.root/'attempt/target-close'
        self.mount=self.root/'remote/supervisor'
        self.route=self.root/'remote/closed'
        for p in (self.handoff/'target-start',self.close,self.mount,self.route/'debt'):
            p.mkdir(parents=True)
        approved=self.write(self.root/'reviewed.json',dict(schema=bridge.CLOSE_REQUEST,reviewed=True,cell_id='cell-c4'))
        original={
            'result':self.write(self.handoff/'result.json',dict(passed=True)),
            'target_start':self.write(self.handoff/'target-start/protocol.jsonl',dict(type='result')),
            'target_cold':self.write(self.handoff/'cold.json',dict(passed=True)),
        }
        remote_request=dict(path=str(self.root/'remote/reviewed.json'),sha256=approved['sha256'])
        self.write(self.close/'intent.json',dict(authorization=dict(reviewed_request=approved,remote_request=remote_request)))
        closed=dict(schema=bridge.CLOSE_RESULT,cell_id='cell-c4',passed=True,all_exited=True,all_unmounted=True,
                    request=remote_request)
        for key in ('ready','process','stopping','stopped'):
            closed[key]=self.write(self.mount/(key+'.json'),dict(stage=key))
        closed['closure']=self.write(self.route/'suite-closure.json',dict(passed=True))
        closed['debt']=dict(batch=self.write(self.route/'debt/result.json',dict(passed=True)),samples=[])
        for i in range(2):
            raw=self.route/'debt'/f'native-{i}.prom'
            raw.write_bytes(b'pending_upload_bytes 0\n')
            closed['debt']['samples'].append(dict(native=bridge.ref(raw),
                sample=self.write(self.route/'debt'/f'sample-{i}.json',dict(sample=i))))
        self.ctx=dict(cell_id='cell-c4',original=original,target_closure=closed,
            target_closure_readback=self.write(self.close/'stdout.json',closed),
            target=dict(mount_directory=str(self.mount),peer=dict(transport='ssh',alias='target',
                repo=str(self.root/'remote/checkout'),python=sys.executable,
                pins=[bridge.ref(Path(sys.executable).resolve(strict=True))])),route=dict(output=str(self.route)))
        mock=patch.object(bridge.transport,'ssh_command',side_effect=self.command)
        self.ssh=mock.start()
        self.addCleanup(mock.stop)

    def write(self,path,value):
        bridge.write_new(path,value)
        return bridge.ref(path)

    def command(self,peer,argv):
        self.assertEqual(peer['alias'],'target')
        self.assertEqual(argv,[sys.executable,'-I','-B','-u','-c',archive.READBACK])
        return argv

    def test_reads_closed_raw_files_and_rechecks_private_archive(self):
        output=self.root/'attempt/archive'
        receipt=archive.archive_handoff(self.ctx,output)
        readback=json.loads(Path(receipt['path']).read_text())
        self.assertEqual(readback['schema'],bridge.ARCHIVE_READBACK)
        self.assertEqual(readback['handoff'],self.ctx['original']['result'])
        self.assertEqual(readback['target_closure_readback'],self.ctx['target_closure_readback'])
        self.assertFalse(readback['uploaded'])
        manifest=json.loads((output/'manifest.json').read_text())
        self.assertEqual(manifest['target/native-0.prom']['sha256'],self.ctx['target_closure']['debt']['samples'][0]['native']['sha256'])
        self.assertIn('target/reviewed-request.json',manifest)
        self.assertIn('handoff/target-start/protocol.jsonl',manifest)
        archive.verify_bundle(output/'handoff.tar.gz',manifest)

    def test_changed_remote_receipt_cannot_create_successful_readback(self):
        (self.mount/'stopped.json').write_bytes(b'changed original\n')
        output=self.root/'attempt/archive'
        with self.assertRaises(subprocess.CalledProcessError):
            archive.archive_handoff(self.ctx,output)
        self.assertFalse((output/'readback.json').exists())
        self.assertTrue((output/'target-readback/ssh.stderr.log').is_file())
