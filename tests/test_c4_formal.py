"""Tiny C4 regression fixtures only: no cloud, FUSE, model or 100k generation."""
import copy
import ast
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
import subprocess
import tempfile
import unittest
from unittest import mock

REPO=Path(__file__).resolve().parents[1]
SCRIPTS=REPO/'experiments/unified-suite/pilot-aliyun/scripts'
sys.path[:0]=[str(REPO),str(REPO/'bench'),str(REPO/'scripts'),str(SCRIPTS)]
from bench.runtime.common import digest,load,write_new
from bench.runtime.fork_work import logical_work,validate_result
from bench.runtime.observe import overhead
from bench.fs_step import fixture
from full_trace_common import tree


def source(name):
    spec=importlib.util.spec_from_file_location('test_'+name.replace('-','_'),SCRIPTS/(name+'.py'))
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value


leaf=source('c4-fork-node');batch=source('run-c4-batch');oracle=source('fork-oracle')


def ref(path):return dict(path=str(path),sha256=digest(path))


class C4Formal(unittest.TestCase):
    def test_import_all_new_entrypoints_without_cloud(self):
        for name in ('run-c4','calibrate-c4','check-c4-calibration','prepare-c4-controller','admit-c4'):
            with self.subTest(name=name):source(name)

    def test_embedded_remote_archive_program_parses(self):
        parsed=ast.parse((SCRIPTS/'run-c4.py').read_text())
        programs=[n.value for n in ast.walk(parsed) if isinstance(n,ast.Constant) and isinstance(n.value,str)
                  and n.value.startswith('import json,pathlib,sys,shutil\n')]
        self.assertEqual(len(programs),1);ast.parse(programs[0])

    def test_fixed_work_has_explicit_copy_and_clone_bytes(self):
        copy_work=logical_work(5,'full-independent-copy');clone_work=logical_work(5,'native-clone')
        self.assertEqual(copy_work['logical_read_bytes']-clone_work['logical_read_bytes'],5*4096)
        self.assertEqual(copy_work['logical_write_bytes']-clone_work['logical_write_bytes'],5*4096)
        self.assertEqual(clone_work['work_tag'],'child-01')
        self.assertEqual(clone_work['logical_write_bytes'],34)

    @unittest.skipUnless(sys.platform.startswith('linux'),'real POSIX five-file copy/fsync/modes')
    def test_different_path_names_have_identical_actual_mutation_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);mount=root/'mnt';mount.mkdir();base=mount/'base';proof=fixture(base,5,4096)
            initial=tree(base);expected=oracle.derive(initial,proof,'child-01',required_files=5)
            asset=root/'asset';asset.mkdir();write_new(asset/'asset.json',{})
            params=dict(files=5,file_bytes=4096,agents=1,children=1)
            cell=dict(id='fixture-cell',params=params)
            value=dict(product='brewfs',mount_point=str(mount),filesystem_binary=ref(Path('/usr/bin/cp')))
            prepared={};independent=dict(first_read=dict(path='d000000/f00000000',sha256=initial['d000000/f00000000']['sha256']))
            results=[]
            for name in ('trial-off-a','formal-longer-name'):
                request=root/(name+'.json');write_new(request,dict(schema='c4-fork-leaf-v1',reviewed=True,
                    asset=str(asset),cell_id=cell['id'],child_name=name,preparation={}))
                out=root/(name+'-evidence')
                with mock.patch.object(leaf,'binding',return_value=(value,cell,base,mount/name)),\
                        mock.patch.object(leaf,'checked_preparation',return_value=(prepared,independent)):
                    leaf.measure(ref(request),out)
                result=load(out/'result.json');clock=load(out/'clock.json')
                validate_result(result,clock,params)
                self.assertEqual(tree(mount/name),expected);self.assertEqual(tree(base),initial)
                self.assertEqual(result['create_to_first_read_seconds'],
                    (clock['first_read_verified_ns']-clock['create_start_ns'])/1e9)
                for name_ in ('create-command.json','create-process.json','create-exit.json','first-read.json','mutation.json'):
                    self.assertTrue((out/name_).is_file())
                results.append({key:result[key] for key in logical_work(5,'full-independent-copy')})
            self.assertEqual(*results)

    def test_continuous_clock_rejects_sum_of_disconnected_subintervals(self):
        clock=dict(zip(('create_start_ns','create_end_ns','first_read_start_ns','first_read_verified_ns','fsync_end_ns','mutation_end_ns'),
                       (1_000_000_000,2_000_000_000,4_000_000_000,5_000_000_000,6_000_000_000,7_000_000_000)))
        value=dict(logical_work(5,'native-clone'),completed=True,passed=True,
            create_command_seconds=1,create_to_first_read_seconds=4,creation_fsync_seconds=1,
            mutation_fsync_seconds=1,wall_seconds=6)
        validate_result(value,clock,dict(files=5,agents=1,children=1))
        bad=dict(value,create_to_first_read_seconds=2)
        with self.assertRaisesRegex(ValueError,'continuous clock'):validate_result(bad,clock,dict(files=5,agents=1,children=1))
        wrong=dict(value,work_tag='attempt-specific')
        with self.assertRaisesRegex(ValueError,'fixed work'):validate_result(wrong,clock,dict(files=5,agents=1,children=1))

    def test_on_off_signature_accepts_paths_but_rejects_different_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);pairs=[]
            for i in range(3):
                pair={}
                for mode in ('off','on'):
                    path=root/(str(i)+mode+'.json')
                    result=dict(passed=True,workload_passed=True,purpose='observer-calibration-'+mode,
                        metric_contract_sha256='a'*64,workload_contract_sha256='b'*64,
                        coordinator_makespan_seconds=1,agents=1,jobs=[dict(passed=True,seconds=1,kind='fork',
                        result=dict(logical_work(5,'native-clone'),child_name=str(i)+mode))])
                    write_new(path,result);pair[mode]=ref(path)
                pairs.append(pair)
            plan=root/'plan.json';body=dict(schema='experiment-observer-overhead-plan-v1',reviewed=True,pairs=pairs,
                metric_contract_sha256='a'*64,workload_contract_sha256='b'*64,max_relative_change=.05)
            write_new(plan,body);self.assertTrue(overhead(ref(plan))['passed'])
            path=Path(pairs[0]['on']['path']);bad=load(path);bad['jobs'][0]['result']['work_tag']='different'
            path.write_text(json.dumps(bad));pairs[0]['on']=ref(path);plan.unlink();write_new(plan,body)
            with self.assertRaisesRegex(ValueError,'changed work'):overhead(ref(plan))

    def test_seven_fresh_names_and_counterbalanced_order(self):
        trials=[dict(mode=m,attempt='child-'+str(i),out='/q/'+str(i)) for i,m in enumerate(('off','on','on','off','off','on'))]
        batch.trial_order(trials,'formal-child')
        with self.assertRaises(ValueError):batch.trial_order(trials,trials[0]['attempt'])
        bad=copy.deepcopy(trials);bad[1]['mode']='off'
        with self.assertRaises(ValueError):batch.trial_order(bad,'formal-child')

    @unittest.skipUnless(sys.platform.startswith('linux'),'actual original PID/start/boot identity')
    def test_failed_trial_keeps_lock_while_original_observer_alive_or_unknown(self):
        from bench.runtime.common import identity
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);state=root/'o'/'trial-01';state.mkdir(parents=True)
            original=identity(os.getpid());write_new(state/'ready.json',dict(process=original))
            code=batch.failure_release_code(str(root),str(REPO),'metadata','/unused',['trial-01'])
            result=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True)
            self.assertEqual(result.returncode,2);self.assertEqual(len(json.loads(result.stdout)['live']),1)
            # Same PID with a different original start tick is not owned.
            (state/'ready.json').write_text(json.dumps(dict(process={**original,'start_ticks':original['start_ticks']+1})))
            self.assertEqual(subprocess.run([sys.executable,'-c',code],capture_output=True).returncode,0)
            (state/'ready.json').unlink()
            result=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True)
            self.assertEqual(result.returncode,2);self.assertEqual(json.loads(result.stdout)['unknown_startup_directories'],[str(state)])

    def test_lease_conflicts_check_each_phase_but_allow_other_pool(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);controls=[root/'canonical',root/'fork'];cell=dict(pool='pool-b',resource_ids=['host:b'])
            plan=dict(config=dict(clusters=[dict(id='pool-b',resource_ids=['host:b'])]))
            for control in controls:
                control.mkdir()
                with sqlite3.connect(control/'state.sqlite') as db:
                    db.executescript('CREATE TABLE cell_leases(cell_id TEXT,pool TEXT,owner TEXT,token TEXT);CREATE TABLE cell_resources(id TEXT,cell_id TEXT);')
                    db.execute('INSERT INTO cell_leases VALUES (?,?,?,?)',('other','pool-a','owner','token'))
                    db.execute('INSERT INTO cell_resources VALUES (?,?)',('host:a','other'))
            batch.ensure_conflicts(controls,cell,plan)
            with sqlite3.connect(controls[0]/'state.sqlite') as db:
                db.execute('INSERT INTO cell_resources VALUES (?,?)',('host:b','other'))
            with self.assertRaisesRegex(ValueError,'intersecting resource'):batch.ensure_conflicts(controls,cell,plan)


if __name__=='__main__':unittest.main()
