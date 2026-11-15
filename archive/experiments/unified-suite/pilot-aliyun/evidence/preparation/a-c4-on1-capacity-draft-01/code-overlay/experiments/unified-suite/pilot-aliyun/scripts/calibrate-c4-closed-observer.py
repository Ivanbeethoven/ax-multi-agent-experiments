#!/usr/bin/env python3
"""Run one pinned C4 calibration with a packing-only change for closed observers."""
import argparse
import hashlib
import importlib.util
import inspect
import json
from pathlib import Path

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


def rewrite(code, helper_source, helper_sha256, worker_hashes):
    """Keep the original remote closure and replace only the pack/copy tail."""
    require(code.count(MARKER) == 1 and code.count(CLOSE) == 1 and code.endswith(TERMINATOR),
            'Pinned original archive closure/packing boundary differs')
    before, after = code.split(MARKER, 1)
    require(before.rstrip().endswith(CLOSE) and "shutil.copyfile(p,target)" in after and
            "archive=pack(staging,names" in after, 'Unexpected original archive packing tail')
    # The original closure and our helper use separate Python namespaces.
    return before + '\n' + 'HELPER_SOURCE=' + repr(helper_source) + '\n' + \
        'HELPER_SHA=' + repr(helper_sha256) + '\n' + 'WORKER_HASHES=' + repr(worker_hashes) + '''
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


def run(request):
    s = load(pinned(request))
    require(s['schema']=='c4-calibration-archive-overlay-v1' and s['reviewed'] is True,
            'Reviewed pinned archive-only overlay required')
    calibrate, runner, source = overlay_inputs(s['calibrate'],s['runner'],s['helper'],s['worker_code'])
    trial = load(pinned(s['trial']))
    require(trial['reviewed'] is True and trial['purpose']=='observer-calibration-on',
            'Packing overlay only applies to an original observer-on calibration')
    original = import_original(calibrate)
    require(Path(original.runner.__file__).resolve()==runner and digest(runner)==s['runner']['sha256'] and
            Path(original.runner.suite_state.__file__).resolve()==original.runner.REPO/'bench/suite_state.py',
            'Loaded runner or suite state does not match the pinned original')
    original.runner.Fork = archive_fork(original.runner.Fork, source, s['helper']['sha256'], s['worker_code'])
    return original.run(s['trial'])


def archive_fork(fork, source, helper_sha256, worker_code):
    require(MARKER in inspect.getsource(fork.archive), 'Unknown original archive implementation')

    class ArchiveOnly(fork):
        _in_archive = False

        def archive(self):
            self._in_archive = True
            try:
                return super().archive()
            finally:
                self._in_archive = False

        def python(self, role, code):
            if self._in_archive:
                code = rewrite(code, source, helper_sha256, worker_code[role])
            return super().python(role, code)

    return ArchiveOnly


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--request', required=True)
    p.add_argument('--sha256', required=True)
    args = p.parse_args()
    print(json.dumps(run(dict(path=args.request, sha256=args.sha256))))