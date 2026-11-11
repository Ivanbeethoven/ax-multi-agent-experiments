"""An immutable handoff cannot launch from a failed or still leased C4 cell."""
import hashlib
import json
from pathlib import Path
import runpy
import sqlite3
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1]/'experiments/unified-suite/pilot-aliyun/scripts/watch-c4-to-c2.py'
VALIDATE = runpy.run_path(str(SCRIPT))['validate']


class OneShotHandoff(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        cell, pool = 'exact-juicefs-cell', 'pool-b'
        self.receipt = dict(schema='c4-pool-handoff-v1',passed=True,safe_for_handoff=True,pool=pool,cell_id=cell,
            next_owner='c2_prepare',collector_name='owned-collector',collector_out='/actual/collector')
        self.recipe = dict(commands=dict(draft_six_and_formal=['--collector-name','owned-collector',
                                                              '--collector-out','/actual/collector']))
        self.receipt['request'] = self.save('original',dict(cell_id=cell,control=str(self.root)))
        self.request = dict(pool=pool,c4_cell=cell,c4_original_request=self.receipt['request'],
                            fork_control=str(self.root),next_owner='c2_prepare')
        sample = self.save('sample',dict(cell_id=cell,uninterrupted=True,recovered=False))
        formal = self.save('formal',dict(cell_id=cell,outcome='complete',sample=sample))
        self.receipt.update(formal_closing=formal, acceptance=self.save('acceptance',dict(accepted=True,sample=sample)),
            closing=self.save('result',dict(passed=True,pool=pool,cell_id=cell,closing=formal,sample=sample)))
        before = dict(UNIT='c4.service',INVOCATION_ID='start-stop',_BOOT_ID='original',
                      __MONOTONIC_TIMESTAMP='1')
        after = dict(before,__MONOTONIC_TIMESTAMP='2',MESSAGE='c4.service: Deactivated successfully.')
        self.receipt['unit'] = dict(name='c4',start=before,stop=after,
                                    systemd=dict(ActiveState='inactive',SubState='dead'))
        self.receipt['boundaries'] = []
        for role in ('client','metadata','objects'):
            info = dict(passed=True,live=[],mounted=[],slot='')
            if role=='objects':
                info['collector'] = dict(running=True,name='owned-collector',out='/actual/collector')
            self.receipt['boundaries'].append(dict(role=role,source=self.save(role,info)))
        with sqlite3.connect(self.root/'state.sqlite') as db:
            db.execute('CREATE TABLE cells(id TEXT,status TEXT,result TEXT)')
            db.execute('INSERT INTO cells VALUES (?,?,?)',(cell,'complete',json.dumps(formal)))
            db.execute('CREATE TABLE cell_leases(pool TEXT,cell_id TEXT)')

    def save(self, name, value):
        p = self.root/(name+'.json')
        p.write_text(json.dumps(value))
        return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())

    def test_actual_original_complete_receipt_can_launch_once(self):
        self.assertEqual(VALIDATE(self.request,self.receipt,self.recipe)['c4_unit'],'c4')

    def test_changed_or_missing_original_role_receipt_rejected(self):
        role = Path(self.receipt['boundaries'][0]['source']['path'])
        role.write_text('{}')
        with self.assertRaisesRegex(ValueError,'evidence changed'):
            VALIDATE(self.request,self.receipt,self.recipe)

    def test_wrong_collector_or_owner_rejected(self):
        for changes in (dict(collector_out='/another'), dict(next_owner='another')):
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                VALIDATE(self.request,dict(self.receipt,**changes),self.recipe)

    def test_existing_b_lease_rejected(self):
        with sqlite3.connect(self.root/'state.sqlite') as db:
            db.execute('INSERT INTO cell_leases VALUES (?,?)',('pool-b','another'))
        with self.assertRaisesRegex(ValueError,'unleased'):
            VALIDATE(self.request,self.receipt,self.recipe)


if __name__ == '__main__':
    unittest.main()
