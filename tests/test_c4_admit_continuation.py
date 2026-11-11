import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bench.runtime.common import digest,write_new


def module(name):
    p=Path(__file__).resolve().parents[1]/'experiments/unified-suite/pilot-aliyun/scripts'/(name+'.py')
    spec=importlib.util.spec_from_file_location(name.replace('-','_'),p)
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);return mod


admit=module('admit-c4')
watcher=module('watch-c4-formal-boundary')


def case(root):
    tag='first';q=root/'q'/tag;q.mkdir(parents=True)
    asset='/srv/ax-client/a/example';plan=root/'plan.json';write_new(plan,{'plan':1})
    purpose='observer-calibration-off';cell={'id':'one'}
    anchor=dict(filesystem='juicefs',storage_identity_sha256='1'*64)
    native=dict(mount_id=277,point=asset+'/mnt',filesystem='fuse.juicefs',source='one')
    inputs=[]
    for index,(suffix,folder,receipt,key) in enumerate((('', 'main','exit.json','native_exit'),
                                                      ('-v','verify-mount','verify-exit.json','verify_exit'))):
        p=q/folder/'mount.reviewed.json';p.parent.mkdir()
        write_new(p,dict(schema='experiment-mount-v1',reviewed=True,anchor=anchor,mount_point=asset+'/mnt',purpose=purpose,
            suite_binding=dict(cell_id=cell['id'],plan=dict(sha256=digest(plan)))))
        spec=dict(path='/srv/ax-client/q/'+tag+'/'+folder+'/mount.reviewed.json',sha256=digest(p))
        d=root/'m'/(tag+suffix);d.mkdir(parents=True)
        process=dict(pid=100+index,boot_id='one',start_ticks=1000+index)
        stop=dict(passed=True,exit_code=0,mount_absent=True,active_workers=[],anchor=anchor,original_mount=native)
        write_new(q/receipt,stop);write_new(d/'stopped.json',stop)
        write_new(d/'ready.json',dict(passed=True,spec=spec,process={**process,'state':'S'},anchor=anchor,mount=native))
        write_new(d/'process.json',dict(spec=spec,process={**process,'state':'R'}))
        write_new(d/'stopping.json',dict(anchor=anchor,original_mount=native,process=process))
        inputs.append((root,q,tag,plan,cell,asset,purpose,key,admit.ref(q/receipt)))
    return inputs


class C4AdmitContinuation(unittest.TestCase):
    def test_binds_original_stops_without_fabricated_cell_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            a,b=case(Path(tmp));v,w=admit.exit_binding(*a),admit.exit_binding(*b)
            self.assertNotEqual(v['identity'],w['identity'])  # kernel can reuse mount ID 277
            self.assertEqual(v['identity'],['one',100,1000])
            wrong=(*a[:-1],b[-1])
            with self.assertRaisesRegex(ValueError,'stop, mount spec or process identity'):admit.exit_binding(*wrong)

    def test_rejects_original_mount_process_identity_changed(self):
        from bench.runtime.common import load
        with tempfile.TemporaryDirectory() as tmp:
            a,_=case(Path(tmp));stopping=Path(tmp)/'m/first/stopping.json';v=load(stopping)
            v['process']['start_ticks']+=1
            stopping.unlink();write_new(stopping,v)
            with self.assertRaisesRegex(ValueError,'process identity'):admit.exit_binding(*a)

    def test_watcher_will_not_follow_replaced_unit(self):
        with patch.object(watcher,'show',return_value={'InvocationID':'different','ActiveState':'active'}):
            with self.assertRaisesRegex(ValueError,'invocation replaced'):
                watcher.wait('ax-pilot-one','original',watcher.time.monotonic()+20)
