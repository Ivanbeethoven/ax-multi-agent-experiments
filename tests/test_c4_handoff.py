import importlib.util
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch


source=Path(__file__).resolve().parents[1]/'experiments/unified-suite/pilot-aliyun/scripts/audit-c4-handoff.py'
spec=importlib.util.spec_from_file_location('c4_handoff_test',source)
handoff=importlib.util.module_from_spec(spec);spec.loader.exec_module(handoff)


class C4Handoff(unittest.TestCase):
    def test_normal_exit_from_same_original_invocation_even_after_unload(self):
        name='ax-pilot-test.service';request=dict(path='/tmp/test',sha256='a'*64);invocation='b'*32
        base=dict(UNIT=name,INVOCATION_ID=invocation,_BOOT_ID='c'*32,__CURSOR='journal-proof',__REALTIME_TIMESTAMP='1234')
        start=dict(base,MESSAGE_ID='39f53479d3a045ac8e11786248231fbf',__MONOTONIC_TIMESTAMP='10',
                   MESSAGE=f"Started {name} --request {request['path']} --sha256 {request['sha256']}.")
        stop=dict(base,MESSAGE_ID='7ad2d189f7e94e70a38c781354912448',__MONOTONIC_TIMESTAMP='20',
                  MESSAGE=name+': Deactivated successfully.')
        unloaded=subprocess.CompletedProcess([],1,stdout='LoadState=not-found\nActiveState=inactive\nSubState=dead\n',stderr='')
        with patch.object(handoff.subprocess,'run',return_value=unloaded),patch.object(handoff.subprocess,'check_output') as journal:
            journal.return_value='\n'.join(map(json.dumps,(start,stop)))
            self.assertEqual(handoff.unit_status(name,request,invocation)['stop']['MESSAGE'],stop['MESSAGE'])
            journal.return_value=json.dumps(start)
            with self.assertRaisesRegex(ValueError,'journal records'):handoff.unit_status(name,request,invocation)

    def test_remote_boundary_is_valid_python(self):
        values=dict(CODE='/srv/code',ROOT='/srv/ax-client',ROLE='client',CELL='case',ASSET='/srv/ax-client/a/case',
                    TAGS=['first'],PREPARATION={'path':'/prep','sha256':'a'*64},COLLECTOR_NAME='unused',COLLECTOR_OUT='/unused')
        compile(handoff.boundary_code(values),'<boundary>','exec')
