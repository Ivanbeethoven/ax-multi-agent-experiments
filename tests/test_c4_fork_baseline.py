"""Tiny offline guards/challenge checks; no cloud, model, FUSE or 100k generation."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO/'bench'), str(REPO/'scripts')]
FILE = REPO/'experiments/unified-suite/pilot-aliyun/scripts/run-c4-fork-baseline.py'
spec = importlib.util.spec_from_file_location('c4_fork_baseline_test', FILE)
tool = importlib.util.module_from_spec(spec); spec.loader.exec_module(tool)
oracle_spec = importlib.util.spec_from_file_location('c4_fork_oracle_test', FILE.with_name('fork-oracle.py'))
oracle = importlib.util.module_from_spec(oracle_spec); oracle_spec.loader.exec_module(oracle)
from bench.fs_step import fixture, mutate
from full_trace_common import tree
from bench.runtime.pilot_pools import active_conflicts, resource_guard
import sqlite3


def cell(product='juicefs'):
    return dict(id='fork-1-'+product, suite='C4', workload='fork', pool='pool-b',
        arm_id='juicefs' if product=='juicefs' else 'native',
        arm=dict(product=product, engine='juicefs' if product=='juicefs' else 'flat-v1'),
        params=dict(files=100000, file_bytes=4096, agents=1, children=1,
                    creation='burst', dataset='fork-fixture-100k-4k'))


class C4ForkBaseline(unittest.TestCase):
    def test_exact_supported_cell_and_reject_c3_or_wrong_size(self):
        for product in ('juicefs', 'brewfs'):
            c = cell(product); plan = dict(provenance_bound=True, cells=[c])
            self.assertEqual(tool.select_cell(plan, c['id'], 'pool-b', product), c)
            for change in (dict(workload='smallfiles'), dict(suite='C3'), dict(pool='pool-a'),
                           dict(params={**c['params'], 'files':1000000}),
                           dict(params={**c['params'], 'children':2})):
                bad = {**c, **change}
                with self.subTest(change=change), self.assertRaises(ValueError):
                    tool.select_cell(dict(plan, cells=[bad]), c['id'], 'pool-b', product)
            with self.assertRaises(ValueError):
                tool.select_cell(dict(plan, provenance_bound=False), c['id'], 'pool-b', product)

    def test_workspace_entity_packed_are_not_silently_copied(self):
        c = cell('brewfs')
        for arm, engine in [('native-workspace','workspace-native-v2'),
                            ('entity-native','workspace-entity-v1'), ('packed','workspace-entity-v1')]:
            bad = {**c, 'arm_id':arm, 'arm':dict(product='brewfs', engine=engine)}
            with self.subTest(arm=arm), self.assertRaises(ValueError):
                tool.select_cell(dict(provenance_bound=True,cells=[bad]), c['id'], 'pool-b','brewfs')

    def test_creation_uses_native_clone_or_forbids_reflinks(self):
        self.assertEqual(tool.creation_argv('juicefs','/bin/jfs','/mnt/base','/mnt/child'),
            ['/bin/jfs','clone','-p','/mnt/base','/mnt/child'])
        self.assertEqual(tool.creation_argv('brewfs','/bin/brewfs','/mnt/base','/mnt/child'),
            ['/usr/bin/cp','-a','--reflink=never','--','/mnt/base','/mnt/child'])
        for base, child in [('/mnt/base','/mnt/base'),('/mnt/base','/elsewhere/child'),('base','child')]:
            with self.assertRaises(ValueError):tool.creation_argv('brewfs','/bin/fs',base,child)

    def test_latest_check_must_match_frozen_build_and_both_branches(self):
        value=dict(passed=True, build_manifest_sha256='a'*64, sources=[
            dict(component=name,matches=True,lookup_exit_code=0,built_commit='commit',remote_commit='commit')
            for name in ('brewfs','rustfs')])
        tool.check_latest(value,'a'*64)
        with self.assertRaises(ValueError):tool.check_latest(value,'b'*64)
        bad=copy.deepcopy(value);bad['sources'][0]['remote_commit']='advanced'
        with self.assertRaises(ValueError):tool.check_latest(bad,'a'*64)

    def test_old_frozen_fs_step_is_refused_before_running_anything(self):
        pins={name:tool.digest(REPO/name) for name in tool.RUNTIME_INPUTS}
        tool.check_runtime(REPO,pins)
        old=dict(pins);old['bench/fs_step.py']='d7597b6160c48b0cd8d4642d9fc8a87a79a328e71e445b4072efe6747089c621'
        with self.assertRaisesRegex(ValueError,'code12'):tool.check_runtime(REPO,old)
        with self.assertRaises(ValueError):tool.check_runtime(REPO,{'bench/fs_step.py':pins['bench/fs_step.py']})

    @unittest.skipUnless(sys.platform.startswith('linux'), 'POSIX copy/mode/fsync check')
    def test_real_five_file_copy_mutation_matches_independent_oracle(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); base = root/'base'; child = root/'child'
            proof = fixture(base,5,4096); original = tree(base)
            expected = oracle.derive(original, proof, 'child-01', required_files=5)
            subprocess.run(tool.creation_argv('brewfs','/unused',base,child),check=True)
            self.assertEqual(tree(child),original)
            mutate(child,'child-01')
            self.assertEqual(tree(child),expected)
            self.assertEqual(tree(base),original)
            self.assertNotEqual((base/'d000000/f00000000').stat().st_ino,
                                (child/'d000000/f00000000').stat().st_ino)

    def test_debt_requires_real_pinned_gauges_and_spacing(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); refs=[]
            for i in range(2):
                value=dict(passed=True,cell_id='fork-1',monotonic_ns=i*1000000000,
                    pending_upload_bytes=0,pending_upload_operations=0,native_gauges={'staging':0})
                path=root/('sample-'+str(i)+'.json');path.write_text(json.dumps(value));refs.append(tool.ref(path))
            target=root/'result.json'
            target.write_text(json.dumps(dict(passed=True,cell_id='fork-1',samples=refs)))
            self.assertEqual(tool.check_debt(root,'fork-1'),tool.ref(target))
            sample=json.loads((root/'sample-1.json').read_text());sample['native_gauges']={}
            (root/'sample-1.json').write_text(json.dumps(sample));refs[1]=tool.ref(root/'sample-1.json')
            target.write_text(json.dumps(dict(passed=True,cell_id='fork-1',samples=refs)))
            with self.assertRaises(ValueError):tool.check_debt(root,'fork-1')

    @unittest.skipUnless(sys.platform.startswith('linux'), 'POSIX canonical resource guards')
    def test_canonical_guard_allows_other_pool_and_blocks_shared_host(self):
        a=dict(pool='pool-a',resource_ids=['host:i-a','disk:d-a'])
        b=dict(pool='pool-b',resource_ids=['host:i-b','disk:d-b'])
        shared=dict(pool='pool-b',resource_ids=['host:i-a','disk:d-b'])
        plan=dict(config=dict(clusters=[dict(id='pool-a',resource_ids=a['resource_ids']),
                                      dict(id='pool-b',resource_ids=b['resource_ids'])]))
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with resource_guard(root,a,plan):
                with resource_guard(root,b,plan):pass
                with self.assertRaises(ValueError):
                    with resource_guard(root,shared,plan):pass
            with sqlite3.connect(root/'state.sqlite') as db:
                db.executescript('CREATE TABLE cell_leases(cell_id TEXT,pool TEXT,owner TEXT,token TEXT);'
                    'CREATE TABLE cell_resources(id TEXT,cell_id TEXT);')
                db.execute('INSERT INTO cell_leases VALUES (?,?,?,?)',('active-a','pool-a','owner','token'))
                db.execute('INSERT INTO cell_resources VALUES (?,?)',('host:i-a','active-a'))
            self.assertEqual(active_conflicts(root,b,plan),[])
            self.assertEqual(len(active_conflicts(root,shared,plan)),1)

    @unittest.skipUnless(sys.platform.startswith('linux'), 'Linux process/mount/cgroup probe')
    def test_release_requires_original_process_exit_and_empty_slot(self):
        expected=dict(pid=99999999,start_ticks=123,boot_id='boot',state='S')
        with tempfile.TemporaryDirectory() as directory:
            slot=Path(directory)/'cgroup.procs';slot.write_text('')
            with mock.patch.object(tool,'SLOT',slot),mock.patch.object(tool,'process_identity',return_value=expected):
                self.assertFalse(tool.release_probe(expected,'/nonexistent-c4-test-mount')['safe_for_release'])
            slot.write_text('456\n')
            with mock.patch.object(tool,'SLOT',slot),mock.patch.object(tool,'process_identity',return_value=None):
                self.assertFalse(tool.release_probe(expected,'/nonexistent-c4-test-mount')['safe_for_release'])
            slot.write_text('')
            # A reused PID is not the original worker, but an occupied cgroup
            # still blocks release through the separate predicate above.
            reused={**expected,'start_ticks':999}
            with mock.patch.object(tool,'SLOT',slot),mock.patch.object(tool,'process_identity',return_value=reused):
                self.assertTrue(tool.release_probe(expected,'/nonexistent-c4-test-mount')['safe_for_release'])


if __name__ == '__main__': unittest.main()
