"""Run small functional probes. Unsupported modes produce a blocked receipt."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
from bench.experiment_modes import CONFIG, MODES, resolve

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--experiment', choices=MODES, required=True)
    p.add_argument('--mode', required=True)
    p.add_argument('--placement', choices=('stay','move'))
    p.add_argument('--brewfs', type=Path, required=True)
    p.add_argument('--producer', type=Path, required=True)
    p.add_argument('--smallfiles', type=Path, required=True)
    p.add_argument('--juicefs', type=Path, required=True)
    p.add_argument('--brewfs-revision', required=True)
    p.add_argument('--brewfs-provenance', type=Path)
    p.add_argument('--rustfs-image', required=True)
    p.add_argument('--rustfs-revision', default='f4ed202508f2153afb7956bae6f7799b60eccd00')
    p.add_argument('--trace', type=Path)
    p.add_argument('--replay', type=Path)
    p.add_argument('--full-trace', action='store_true', help='Use complete trace counts, links and SQLite verification')
    p.add_argument('--trace-timeout', type=int, default=300, help='Seconds for each full trace qualification step')
    p.add_argument('--checkpoint',type=Path)
    p.add_argument('--opencode',type=Path)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    for name in ('brewfs','producer','smallfiles','juicefs','trace','replay','out','brewfs_provenance','checkpoint','opencode'):
        value = getattr(a,name)
        if value is not None:
            setattr(a,name,value.resolve())
    try:
        plan = resolve(json.loads(CONFIG.read_text()), a.experiment, a.mode, a.placement)
    except ValueError as e:
        p.error(str(e))
    a.out.mkdir(mode=0o700)
    receipt = {'plan':plan, 'formal_sample':False, 'status':'blocked'}
    scripts = CONFIG.parent.parent / 'pilot-aliyun/scripts'
    supported = (a.mode in ('brewfs-full','juicefs') and a.experiment in ('C3-smallfiles','C4-fork')) or a.experiment in ('C1-io','C1-layout') or (a.experiment == 'C2-replay' and a.mode in ('brewfs-basic','brewfs-full','juicefs','dynamic-off'))
    supported = supported or (a.experiment=='C4-handoff' and a.placement=='stay' and a.checkpoint and a.opencode)
    if a.experiment=='C2-runtime' and a.brewfs_provenance:
        from bench.runtime.packed_read_candidate import verify
        try:
            pins=verify(a.brewfs,a.producer,a.brewfs_provenance,a.brewfs_revision)
        except (OSError,ValueError) as error:
            receipt.update(status='failed',reason=str(error))
            (a.out/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
            return 2
        cmd=[sys.executable,str(scripts/'qualify-packed-read-local.py'),
             '--brewfs',str(a.brewfs),'--brewfs-sha256',pins['brewfs'],
             '--producer',str(a.producer),'--producer-sha256',pins['producer'],
             '--rustfs-image',a.rustfs_image,'--rustfs-revision',a.rustfs_revision,
             '--arm','overlay-read' if a.mode=='brewfs-full' else 'lower-read',
             '--out',str(a.out/'run')]
        receipt['runtime_scope']='read-only extents, lookup, non-root getattr and directory opening; no writable toggle claim'
        receipt['provenance']=str(a.brewfs_provenance)
    elif supported:
        script = scripts / ('rbm-lifecycle.py' if a.experiment != 'C4-fork' else 'rbm-native-fork.py')
        cmd = [sys.executable, str(script), '--product',
               'juicefs' if a.mode == 'juicefs' else 'brewfs']
        for name in ('brewfs','producer','juicefs','rustfs_image'):
            cmd += ['--'+name.replace('_','-'), str(getattr(a,name))]
        if a.experiment != 'C4-fork':
            cmd += ['--smallfiles',str(a.smallfiles), '--workload', 'c1' if a.experiment.startswith('C1') else 'c2' if a.experiment == 'C2-replay' else 'c3', '--hint', plan['brewfs_hint'] or 'auto', '--layout', plan['rustfs_layout_profile']]
            cmd += ['--engine', 'flat' if plan['engine'] == 'flat' else 'workspace']
            if a.experiment=='C4-handoff':
                cmd[cmd.index('--workload')+1]='c4-stay'
                cmd += ['--checkpoint',str(a.checkpoint),'--opencode',str(a.opencode)]
                receipt['runtime_scope']='same-host OpenCode state reopen with empty HOME; no model continuation'
            if a.experiment == 'C2-replay':
                if not a.trace or not a.replay:
                    p.error('C2 requires --trace and --replay')
                cmd += ['--trace',str(a.trace),'--replay',str(a.replay)]
                if a.full_trace:
                    cmd += ['--full-trace','--trace-timeout',str(a.trace_timeout)]
        else:
            cmd += ['--brewfs-revision' ,a.brewfs_revision]
            if a.brewfs_provenance:
                cmd += ['--brewfs-provenance',str(a.brewfs_provenance)]
        cmd += ['--out',str(a.out/'run')]
    else:
        cmd=None
    if cmd is not None:
        receipt['argv'] = cmd
        # The child owns service teardown; don't kill its cleanup with an outer timeout.
        proc = subprocess.run(cmd)
        receipt['exit_code'] = proc.returncode
        try:
            evidence = json.loads((a.out/'run/receipt.json').read_text())
        except (OSError, ValueError) as error:
            evidence = {'passed':False,'error':str(error)}
        receipt['status'] = 'passed' if evidence['passed'] and proc.returncode == 0 else 'failed'
        receipt['evidence'] = evidence
    else:
        receipt['reason'] = ('qualified read-control binary and --brewfs-provenance required' if a.experiment=='C2-runtime' else
                            'same-engine toggle not implemented' if a.mode in
                            ('overlay-off','packed-off','all-off') else
                            'this workload adapter is not yet implemented in the minimal runner')
    (a.out/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    return 0 if receipt['status']=='passed' else 2

if __name__ == '__main__':
    raise SystemExit(main())
