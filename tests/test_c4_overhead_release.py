import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

from bench.runtime.common import load,write_new

script=Path(__file__).resolve().parents[1]/'experiments/unified-suite/pilot-aliyun/scripts/audit-c4-overhead-release.py'
spec=importlib.util.spec_from_file_location('c4_overhead_release_test',script)
audit=importlib.util.module_from_spec(spec);spec.loader.exec_module(audit)


def gate(root):
    rows=[];over=root/'overhead';over.mkdir()
    for i,mode in enumerate(('off','on','on','off','off','on')):
        p=root/f'cohort-{i}.json';duration=100 if mode=='off' else 106
        job=dict(kind='fork',passed=True,seconds=duration,result=dict(files=100000,file_bytes=4096,children=1,
            work_tag='child-01',method='native-clone',logical_read_bytes=4096,logical_write_bytes=34,fsync_files=100000))
        write_new(p,dict(passed=True,workload_passed=True,purpose='observer-calibration-'+mode,agents=1,jobs=[job],
            coordinator_makespan_seconds=duration,metric_contract_sha256='a',workload_contract_sha256='b'))
        rows.append(dict(cohort=audit.ref(p),client_verify={'path':str(root/f'check-{i}.json'),'sha256':str(i)*64}))
    pairs=[dict(off=rows[i]['cohort'],on=rows[j]['cohort']) for i,j in ((0,1),(3,2),(4,5))]
    plan=over/'plan.json';write_new(plan,dict(schema='experiment-observer-overhead-plan-v1',reviewed=True,
        pairs=pairs,max_relative_change=.05,metric_contract_sha256='a',workload_contract_sha256='b'))
    verify=over/'verification.json';write_new(verify,dict(policy='touched-cold-v1',
        trials=[dict(proof=r['client_verify']) for r in rows]))
    result=audit.overhead(audit.ref(plan));write_new(over/'result.json',dict(result,verification=audit.ref(verify)))
    write_new(root/'failed.json',dict(passed=False,error=audit.REJECTION,automatic_retry=False))
    return rows


class C4OverheadRelease(unittest.TestCase):
    def test_exact_failed_gate_recomputes_three_original_pairs(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);rows=gate(root)
            checked=audit.rejected_gate(root,rows)
            self.assertAlmostEqual(checked['relative_changes']['makespan'],.06)
            rows[1],rows[2]=rows[2],rows[1]
            with self.assertRaisesRegex(ValueError,'six exact cohort'):audit.rejected_gate(root,rows)

    def test_terminal_probe_must_be_latest_and_all_roles_clear(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);old=root/'failure-boundaries-1';new=root/'failure-boundaries-2';old.mkdir();new.mkdir()
            write_new(root/'failed.json',{});os.utime(root/'failed.json',ns=(1_000_000_000,1_000_000_000))
            stop=dict(end={'__REALTIME_TIMESTAMP':'2000000'})
            for directory in (old,new):
                for role in audit.ROLES:
                    stdout=directory/(role+'-stdout');status=directory/(role+'-exit.json')
                    write_new(stdout,dict(safe_for_release=True,live=[],mounts=[],unknown_startup_directories=[],slot='',
                                          original_processes=[dict(pid=1)]));write_new(status,dict(exit_code=0))
                    os.utime(stdout,ns=(1_500_000_000,1_500_000_000));os.utime(status,ns=(1_600_000_000,1_600_000_000))
            self.assertTrue(all('failure-boundaries-2' in v['source']['path'] for v in audit.boundaries(root,stop)))
            stdout=new/'client-stdout';value=load(stdout);value['slot']='1';stdout.unlink();write_new(stdout,value)
            os.utime(stdout,ns=(1_500_000_000,1_500_000_000))
            with self.assertRaisesRegex(ValueError,'not clear: client'):audit.boundaries(root,stop)
