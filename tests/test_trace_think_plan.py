import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'harness'))
from trace_think_plan import allocate, prepare


def save(path,value): path.write_text(json.dumps(value),encoding='utf-8')


def fixture(root):
    events=root/'events.jsonl'; evidence=root/'turns.json'; boundary=root/'boundaries.json'
    blobs=root/'blobs'; blobs.mkdir(); content=b'actual captured bytes\n'; sha=hashlib.sha256(content).hexdigest()
    (blobs/sha).write_bytes(content)
    rows=[('init',[],None),('create',['/file',420],1),
          ('write',['/file',{'blob':sha,'len':len(content)},0,1],len(content)),
          ('fsync',['/file',0,1],0),('release',['/file',1],0),('destroy',[],None)]
    events.write_bytes(b''.join((json.dumps({'seq':i,'op':op,'args':args,'result':result,'errno':0})+'\n').encode()
                               for i,(op,args,result) in enumerate(rows,1)))
    save(evidence,{'synthetic_test_only':True,'tool_boundaries':[2,4]})
    save(boundary,{'schema':'agent-boundaries-v1','reviewed':True,
         'events_sha256':hashlib.sha256(events.read_bytes()).hexdigest(),
         'evidence':[{'path':evidence.name,'sha256':hashlib.sha256(evidence.read_bytes()).hexdigest()}],
         'boundaries':[{'before_seq':seq,'kind':'tool','id':str(seq),'evidence_index':0,
                        'evidence_locator':'tool_boundaries'} for seq in [2,4]]})
    return events,boundary,blobs,content


class AllocationTests(unittest.TestCase):
    def test_bounded_exact_budget_reproducible_and_distinct_agents(self):
        for count in (1,2,8,64,257):
            for budget in (0,1,count*7500,count*15000-1,count*15000):
                for seed in range(10):
                    a=allocate(count,budget,seed,'agent-01')
                    self.assertEqual(sum(a),budget); self.assertEqual(len(a),count)
                    self.assertTrue(all(0<=v<=15000 for v in a))
                    self.assertEqual(a,allocate(count,budget,seed,'agent-01'))
        self.assertNotEqual(allocate(64,480000,42,'a'),allocate(64,480000,42,'b'))

    def test_infeasible_and_single_agent_rejected(self):
        for count,budget in [(0,0),(1,15001),(2,-1)]:
            with self.assertRaises(ValueError):allocate(count,budget,42,'a')
        with tempfile.TemporaryDirectory() as tmp:
            events,boundary,_,_=fixture(Path(tmp))
            with self.assertRaises(ValueError):prepare(events,boundary,10,42,'a',1)

    def test_provenance_trace_binding_and_lifecycle_boundaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); events,boundary,_,_=fixture(root)
            p=prepare(events,boundary,15000,42,'a',64)
            self.assertEqual(sum(b['delay_ms'] for b in p['boundaries']),15000)
            original=json.loads(boundary.read_text())
            for field,value in [('reviewed',False),('events_sha256','0'*64)]:
                modified=dict(original); modified[field]=value; save(boundary,modified)
                with self.assertRaises(ValueError):prepare(events,boundary,10,42,'a',64)
            save(boundary,original); (root/'turns.json').write_bytes(b'changed')
            with self.assertRaises(ValueError):prepare(events,boundary,10,42,'a',64)


@unittest.skipUnless(os.environ.get('AX_PACED_REPLAY_BINARY'),'set AX_PACED_REPLAY_BINARY for Linux binary integration')
class ReplayTests(unittest.TestCase):
    def run_replay(self,root,paced=False,bad=False,strict=False):
        events,boundary,blobs,content=fixture(root)
        dest=root/'dedicated'; dest.mkdir(); output=root/'result.json'
        argv=[os.environ['AX_PACED_REPLAY_BINARY'],str(events),str(blobs),str(dest),str(output)]
        if not strict:argv+=['--performance']
        if paced:
            p=prepare(events,boundary,80,42,'agent-01',64)
            if bad=='budget':p['total_think_ms']=81
            if bad=='hash':p['events_sha256']='0'*64
            if bad=='lifecycle':p['boundaries'][0]['before_seq']=1
            schedule=root/'think.json';save(schedule,p);argv+=['--simulate-llm',str(schedule)]
        result=subprocess.run(argv,capture_output=True,text=True)
        if bad:
            self.assertNotEqual(result.returncode,0);self.assertFalse(output.exists());self.assertEqual(list(dest.iterdir()),[])
            return
        self.assertEqual(result.returncode,0,result.stderr)
        data=json.loads(output.read_text());self.assertEqual((dest/'file').read_bytes(),content)
        self.assertEqual(data['operations'],6);self.assertEqual(data['actual_write_bytes'],len(content))
        self.assertEqual(data['remaining_handles'],0);self.assertTrue(data['completed'])
        self.assertEqual(data['valid'],strict)
        self.assertEqual(data['think_time']['enabled'],paced)
        if paced:
            self.assertEqual(data['think_time']['planned_total_ms'],80)
            self.assertEqual(data['think_time']['applied_boundaries'],2)
            self.assertGreaterEqual(data['think_time']['actual_sleep_seconds'],0.08)
            self.assertAlmostEqual(data['execution_seconds']-data['active_replay_seconds'],data['think_time']['actual_sleep_seconds'],places=7)
        else:self.assertEqual(data['execution_seconds'],data['active_replay_seconds'])

    def test_default_off_paced_same_bytes_and_bad_plan_no_mutation(self):
        for paced,bad in [(False,False),(True,False),(True,'budget'),(True,'hash'),(True,'lifecycle')]:
            with tempfile.TemporaryDirectory() as tmp:self.run_replay(Path(tmp),paced,bad)

    def test_strict_mode_still_checks_same_content_with_waits(self):
        with tempfile.TemporaryDirectory() as tmp:self.run_replay(Path(tmp),True,strict=True)


if __name__=='__main__':unittest.main()
