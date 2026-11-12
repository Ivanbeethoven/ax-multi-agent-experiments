#!/usr/bin/env python3
"""Run one pinned C4 calibration with a packing-only change for closed observers."""
import argparse
from contextlib import contextmanager
import hashlib
import importlib.util
import inspect
import json
from pathlib import Path
import subprocess

MARKER = "\nstaging=root/'evidence'/TAG;staging.mkdir(parents=True);names=[]"
CLOSE = "write_new(q/'result.json',result);DIRS.append('q/'+TAG+'-closure')"
TERMINATOR = "print(json.dumps(dict(closure=result,archive=archive)))"
ROLES = ('client', 'metadata', 'objects')
CODE_FILES = {
    'bench/__init__.py', 'bench/archive_check.py', 'bench/experiment_io.py', 'bench/experiment_step.py',
    'bench/runtime/__init__.py', 'bench/runtime/common.py', 'bench/runtime/observe.py',
}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def load(path):
    return json.loads(Path(path).read_bytes())


def pinned(value):
    path = Path(value['path'])
    require(path.is_absolute() and not path.is_symlink() and digest(path)==value['sha256'],
            'Changed or non-absolute pinned file')
    return path


def rewrite(code, helper_source, helper_sha256, worker_hashes, scratch=None, observer_format=None):
    """Keep the original remote closure and replace only the pack/copy tail."""
    require(code.count(MARKER) == 1 and code.count(CLOSE) == 1 and code.endswith(TERMINATOR),
            'Pinned original archive closure/packing boundary differs')
    before, after = code.split(MARKER, 1)
    require(before.rstrip().endswith(CLOSE) and "shutil.copyfile(p,target)" in after and
            "archive=pack(staging,names" in after, 'Unexpected original archive packing tail')
    # The original closure and our helper use separate Python namespaces.
    return before + '\n' + 'HELPER_SOURCE=' + repr(helper_source) + '\n' + \
        'HELPER_SHA=' + repr(helper_sha256) + '\n' + 'WORKER_HASHES=' + repr(worker_hashes) + '\n' + \
        'OBSERVER_SCRATCH=' + repr(scratch) + '\n' + 'OBSERVER_FORMAT=' + repr(observer_format) + '''
import hashlib
if hashlib.sha256(HELPER_SOURCE.encode('utf-8')).hexdigest()!=HELPER_SHA:
 raise ValueError('Pinned archive-only helper bytes differ')
if not HAS_OBSERVER:
 raise ValueError('Archive-only overlay cannot run without an observer')
scope={'__name__':'c4_archive_closed_observer'}
exec(compile(HELPER_SOURCE,'<pinned archive-c4-closed-observer.py>','exec'),scope)
def evidence(p):
 return dict(path=str(p),sha256=digest(p))
spec=dict(schema='c4-closed-observer-archive-v1',reviewed=True,root=ROOT,role=ROLE,
 tag=TAG,cell_id=CELL,has_observer=True,directories=DIRS,code=CODE,
 code_pins=[dict(path=str(pathlib.Path(CODE)/p),sha256=h) for p,h in sorted(WORKER_HASHES.items())],
 observer_ready=evidence(root/'o'/TAG/'ready.json'),observer_finished=evidence(root/'o'/TAG/'finished.json'),
 role_closure=evidence(q/'result.json'))
if OBSERVER_SCRATCH:spec['observer_scratch']=OBSERVER_SCRATCH
if OBSERVER_FORMAT:spec['observer_format']=OBSERVER_FORMAT
print(json.dumps(scope['archive'](spec)))
'''


def import_original(path):
    spec = importlib.util.spec_from_file_location('c4_original_calibration', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def overlay_inputs(calibrate_pin, runner_pin, helper_pin, worker_code):
    calibrate = pinned(calibrate_pin)
    runner = pinned(runner_pin)
    require(calibrate.with_name('run-c4.py')==runner, 'Original calibration must use the pinned sibling runner')
    return calibrate, runner, archive_inputs(helper_pin,worker_code)


def archive_inputs(helper_pin,worker_code):
    helper = pinned(helper_pin)
    raw = helper.read_bytes()
    require(hashlib.sha256(raw).hexdigest()==helper_pin['sha256'], 'Archive helper changed')
    source = raw.decode('utf-8')
    for role in ROLES:
        values = worker_code[role]
        require(set(values)==CODE_FILES and all(isinstance(h,str) and len(h)==64 and
                all(c in '0123456789abcdef' for c in h) for h in values.values()), 'Original worker CODE hashes required')
    require(set(worker_code)==set(ROLES), 'Exact three original worker role pins required')
    return source


def format_inputs(spec):
    """Read only the exact two reviewed sources in the new coordinator overlay."""
    if spec is None:
        return None
    producer = spec.get('producer')
    keys = {'producer','audit_module'} if producer=='ndjson' else {'producer','audit_module','fold_tool','fold_peak_bytes'}
    require(producer in ('legacy-fold','ndjson') and set(spec)==keys and
            (producer=='ndjson' or type(spec['fold_peak_bytes']) is int and spec['fold_peak_bytes']>0),
            'Exact reviewed C4 observer format and any fold peak budget required')
    script = Path(__file__).resolve()
    expected = dict(audit_module=script.parents[4]/'bench/runtime/observe_ndjson.py')
    bundle = {'producer':producer}
    if producer=='legacy-fold':
        expected['fold_tool'] = script.with_name('fold-c4-observer.py')
        bundle['fold_peak_bytes'] = spec['fold_peak_bytes']
    for name,path in expected.items():
        source = pinned(spec[name])
        require(source==path, 'Pinned C4 observer format not in the overlay: '+name)
        raw = source.read_bytes()
        require(hashlib.sha256(raw).hexdigest()==spec[name]['sha256'],
                'Pinned C4 observer format source changed')
        bundle[name] = dict(source=raw.decode('utf-8'),sha256=spec[name]['sha256'])
    return bundle


EXEC_NODE = "os.execv(sys.executable,[sys.executable,'-u','-m',x['module'],*x['args']])\n"
LOAD_OBSERVER = """
import hashlib,sys,types
if hashlib.sha256(source.encode('utf-8')).hexdigest()!=sha:
    raise ValueError('Pinned NDJSON collector source changed')
sys.path.insert(0,str(root))
collector=types.ModuleType('bench.runtime.observe_ndjson')
collector.__package__='bench.runtime'
exec(compile(source,'<pinned NDJSON collector>','exec'),collector.__dict__)
"""
START_OBSERVER = """
source=x['source'];sha=x['sha256'];spec=x['spec'];output=x['output']
""" + LOAD_OBSERVER + """
args='source,sha,root,spec,output='+repr((source,sha,str(root),spec,output))
serve=args+'\\n'+%r+"\\ncollector.serve(spec,output,format_sha256=sha)"
value=collector.start(spec,output,serve_argv=[sys.executable,'-B','-c',serve],repo=root)
print(json.dumps(dict(type='result',**value)))
""" % LOAD_OBSERVER


def start_ndjson(peer_ref, state, output, spec_ref, payload):
    """Use the original SSH/runtime pin checks, only swapping the final action."""
    from bench.runtime import common, transport
    from bench.ssh_inventory import linux_path
    peer = common.load(common.pinned(peer_ref))
    require(not peer.get('container') and not peer.get('cgroup'), 'NDJSON must run on the original observer host')
    linux_path(state)
    linux_path(spec_ref['path'])
    require(transport.BOOTSTRAP.endswith(EXEC_NODE), 'Unexpected original SSH runtime pin bootstrap')
    boot = transport.BOOTSTRAP.removesuffix(EXEC_NODE)+START_OBSERVER
    directory = common.private_dir(output)
    request = dict(repo=peer['repo'],pins=peer['pins'],module='bench.runtime.node',cgroup=None,
                   source=payload['source'],sha256=payload['sha256'],spec=spec_ref,output=state)
    argv = transport.ssh_command(peer,[peer['python'],'-u','-c',boot])
    common.write_new(directory/'format.json',dict(producer='ndjson',sha256=payload['sha256'],
                     peer=peer_ref,spec=spec_ref,output=state,automatic_retry=False))
    with (directory/'ssh.stderr.log').open('xb') as err, (directory/'protocol.jsonl').open('xb') as out:
        result = subprocess.run(argv,input=(json.dumps(request,separators=(',',':'))+'\n').encode(),
                                stdout=out,stderr=err)
    require(result.returncode==0, 'Pinned NDJSON observer start failed; retain diagnostics, do not retry')
    value = load(directory/'protocol.jsonl')
    require(value['type']=='result' and value['passed'] is True, 'Pinned NDJSON observer did not become ready')
    return value


@contextmanager
def archive_audit(helper_source, observer_format, *callers):
    """Keep old callers byte-identical and only dispatch streams within this C4 call."""
    if observer_format is None:
        yield
        return
    from bench.runtime import observe
    helper = {'__name__':'pinned_c4_archive'}
    exec(compile(helper_source,'<pinned C4 archive helper>','exec'),helper)
    ndjson = helper['format_module'](observer_format['audit_module'],
                                     'bench.runtime.observe_ndjson','bench.runtime')
    targets = (observe,*callers)
    original = [(module,module.audit) for module in targets]

    def dispatch(directory, max_gap_seconds, marks):
        # Calling NDJSON's own legacy fallback after replacing observe.audit
        # would recurse. Keep the original callable for untouched trees.
        stream = Path(directory)/ndjson.STREAM
        if stream.is_symlink() or stream.exists() and not stream.is_file():
            raise ValueError('Unexpected C4 observation stream path')
        if stream.is_file():
            return ndjson.audit(directory,max_gap_seconds,marks)
        return original[0][1](directory,max_gap_seconds,marks)

    try:
        for module,_ in original:
            module.audit = dispatch
        yield
    finally:
        for module,audit in original:
            module.audit = audit


def run(request):
    s = load(pinned(request))
    require(s['schema']=='c4-calibration-archive-overlay-v1' and s['reviewed'] is True,
            'Reviewed pinned archive-only overlay required')
    calibrate, runner, source = overlay_inputs(s['calibrate'],s['runner'],s['helper'],s['worker_code'])
    trial = load(pinned(s['trial']))
    require(trial['reviewed'] is True and trial['purpose']=='observer-calibration-on',
            'Packing overlay only applies to an original observer-on calibration')
    plan = load(Path(trial['control'])/'suite/plan.json')
    # The overlay can be installed independently of the original runtime.
    cell = next(c for c in plan['cells'] if c['id']==trial['cell_id'])
    arm = cell.get('arm') or {}
    if (cell.get('suite'), cell.get('workload'), cell.get('arm_id'),
            arm.get('product'), arm.get('engine')) == ('C4', 'fork', 'native', 'brewfs', 'flat-v1'):
        raise ValueError('Historical C4 BrewFS Flat full-copy dispatch is retired; existing evidence remains readable')
    original = import_original(calibrate)
    require(Path(original.runner.__file__).resolve()==runner and digest(runner)==s['runner']['sha256'] and
            Path(original.runner.suite_state.__file__).resolve()==original.runner.REPO/'bench/suite_state.py',
            'Loaded runner or suite state does not match the pinned original')
    original.runner.Fork = archive_fork(original.runner.Fork, source, s['helper']['sha256'], s['worker_code'],
                                        s.get('observer_scratch'), original.runner.common.ROOTS,
                                        format_inputs(s.get('observer_format')))
    return original.run(s['trial'])


def archive_fork(fork, source, helper_sha256, worker_code, scratch=None, common_roots=None, observer_format=None):
    require(MARKER in inspect.getsource(fork.archive), 'Unknown original archive implementation')
    direct = observer_format and observer_format['producer']=='ndjson'
    require(not direct or scratch, 'Pinned direct NDJSON producer requires dedicated observer scratch')
    if scratch:
        require(scratch['root']=='/srv/ax-c4-observer-scratch' and common_roots is not None and
                common_roots['objects']=='/srv/ax-objects-0', 'Pinned original object root required for scratch routing')

    class ArchiveOnly(fork):
        _in_archive = False

        def execute(self, stage):
            if stage!='observe-start' or not scratch:
                return super().execute(stage)
            require(self.purpose=='observer-calibration-on' or
                    self.purpose=='experiment' and observer_format is not None,
                    'Scratch route requires the original observed calibration or pinned formal overlay')
            code = 'scope={"__name__":"scratch_preflight"}\nexec('+repr(source)+',scope)\n' + \
                   'scope["verify_scratch"]('+repr(scratch)+')\nprint("scratch verified")'
            self.python('objects',code)
            before = dict(common_roots)
            common_roots['objects'] = scratch['root']
            from bench.runtime import fleet
            original_call = fleet.call
            started = False

            def call(peer, action, state, output, spec_ref=None, label=None):
                nonlocal started
                if action=='observe-start' and Path(output)==self.out/'observer-start'/'objects':
                    require(not started and state==scratch['root']+'/o/'+self.tag,
                            'Unexpected original object observer route')
                    started = True
                    return start_ndjson(peer,state,output,spec_ref,observer_format['audit_module'])
                return original_call(peer,action,state,output,spec_ref,label)

            if direct:
                fleet.call = call
            try:
                result = super().execute(stage)
                require(not direct or started, 'The original observer stage did not start the pinned objects producer')
                return result
            finally:
                if direct:
                    fleet.call = original_call
                common_roots.clear(); common_roots.update(before)

        def archive(self):
            self._in_archive = True
            try:
                with archive_audit(source,observer_format):
                    return super().archive()
            finally:
                self._in_archive = False

        def python(self, role, code):
            if self._in_archive:
                storage = scratch if role=='objects' else None
                if storage:
                    marker = "ROOT='/srv/ax-objects-0'\n"
                    require(code.count(marker)==1, 'Original object archive root assignment differs')
                    code = code.replace(marker,'ROOT='+repr(storage['root'])+'\n',1)
                code = rewrite(code, source, helper_sha256, worker_code[role],storage,observer_format)
            return super().python(role, code)

    return ArchiveOnly


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--request', required=True)
    p.add_argument('--sha256', required=True)
    args = p.parse_args()
    print(json.dumps(run(dict(path=args.request, sha256=args.sha256))))
