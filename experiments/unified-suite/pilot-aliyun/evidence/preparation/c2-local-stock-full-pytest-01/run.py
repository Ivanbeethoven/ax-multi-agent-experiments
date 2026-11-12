#!/usr/bin/env python3
"""Exact one-host stock workspace-v1 trace diagnostic, using pinned local harness."""
import hashlib
from pathlib import Path
import sys

base = Path('/mnt/c/Users/HXY/.cache/ax-pilot-20261111/publish-fsync-1911-01')
fixture = base/'experiments/unified-suite/pilot-aliyun/evidence/preparation/c4-workspace-v1-packed-fork-01/smoke.py'
raw = fixture.read_bytes()
pin = '5e0cb515cb07851e2f8713ce78395fbf2b47796ad7a7c534eaea828ecd066e2d'
if hashlib.sha256(raw).hexdigest() != pin:
    raise SystemExit('Private local harness changed')
source = raw.decode().replace('\r\n', '\n')
for old, new in [
    ('ax-c4-packed-fork-', 'ax-c2-stock-full-'), ('ax.c4.packed-fork.owner','ax.c2.stock-full.owner'),
    ("producer = binary_root / 'packed_v3_snapshot_fixture'", "producer = Path('/home/hxy/ax-unified-pilot-prep/tools/parent-fd-cache-01/full-fuse-replay')"),
    ('aa45b1ee9640da92051ead72b17ed41836b57812e05623f860b963e09373b3ab', '35c45abd910d826ea17b5d6dfc750c7083cda5e8ce8f3bbe5ef77a78ffbea1aa'),
    ("schema='c4-local-brewfs-packed-fork-smoke-v1',passed=False,files=2", "schema='c2-local-stock-workspace-v1-full-trace-v1',passed=False"),
    ('One WSL FUSE host, tiny nonempty packed-v3 lower, no performance timing.', 'One WSL FUSE host, original pytest-full-02 trace, ordinary stock workspace-v1 without a Packed lower, no performance acceptance.'),
    ('+ 138', '+ 420'), ('alarm(145)', 'alarm(430)'),
]:
    if old not in source:
        raise SystemExit('Unexpected harness structure')
    source = source.replace(old,new)

# Preserve exact local backend isolation, mount configuration and cleanup; replace
# only the tiny fixture's workload and check. Direct children share an owned PGID.
start = source.index("    py = 'from pathlib import Path")
end = source.index('\ntry:',start)
source = source[:start] + '''
    return

def foreground(label, argv, limit):
    t0 = time.monotonic()
    out = result_path.parent
    dest = out/(label+'-foreground.log')
    with dest.open('xb') as f:
        child = subprocess.Popen(list(map(str, argv)), env=env, stdout=f, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            code = child.wait(timeout=max(.1,min(limit,deadline-time.monotonic())))
        except BaseException:
            try: os.killpg(child.pid,signal.SIGTERM)
            except ProcessLookupError: pass
            try: child.wait(timeout=2)
            except subprocess.TimeoutExpired: pass
            try: os.killpg(child.pid,signal.SIGKILL)
            except ProcessLookupError: pass
            child.wait(timeout=4)
            result.setdefault('stages',{})[label]=dict(exit_code=child.returncode,wall_seconds=time.monotonic()-t0,interrupted=True)
            raise
    result.setdefault('stages',{})[label]=dict(exit_code=code,wall_seconds=time.monotonic()-t0)
    if code: raise RuntimeError(label+' exited '+str(code)+': '+clean_text(dest.read_text(errors='replace')))
''' + source[end:]
start = source.index("    src=work/'src'")
end = source.index('\nexcept Exception',start)
source = source[:start] + '''
    case=Path('/home/hxy/ax-unified-pilot-prep/inputs/cases/pytest-full-02')
    qual=Path('/mnt/c/Users/HXY/.cache/ax-pilot-20261111/publish-fsync-1911-01/experiments/unified-suite/pilot-aliyun/scripts/qualify-trace.py')
    out=result_path.parent
    phase='record-actual-case'
    with (case/'initial-tree.json').open('rb') as stream: initial_sha=hashlib.file_digest(stream,'sha256').hexdigest()
    if initial_sha!='c1b9b066e6660301ad944fff78de1910e6388021c6caa90923355848eb1c2847':
        raise RuntimeError('Unexpected initial-tree pin')
    result.update(case='pytest-full-02',initial_tree_sha256=initial_sha,
        archive_sha256='9953d90517eaf520814150f80abccb11f9d74b7ac84ecc56fa394a5f8495aa3d',
        preflight_initial_entries=714,volume_format='workspace-v1',packed_lower=False,
        portable_terminal_probe=True,cache_parent_fds=True,automatic_retry=False,wall_limit_seconds=420)
    data=dict(backend='s3',s3=dict(bucket=bucket,endpoint=endpoint,region='us-east-1',
                force_path_style=True,rustfs_ec_block_size_hint='auto'))
    config=work/'data.json';config.write_text(json.dumps({'data':data}))
    namespace='c2-stock-full-'+uuid.uuid4().hex[:10]
    prefix=[binary,'workspace','--meta-backend','redis','--meta-url',rurl,'--workspace-namespace',namespace]
    phase='init-ordinary-workspace-v1'
    parent=json_call(prefix,'init-volume','--data-config',config)
    ws=parent['workspace_id']
    result['workspace_id']=ws
    result['packed_binding_absent']=not binding(ws)
    if not result['packed_binding_absent']: raise RuntimeError('Unexpected Packed binding')
    phase='writer-mount'
    visit('writer',ws,data,rurl,namespace)
    run_root=point/'trace-root'
    phase='import-exact-source'
    foreground('import',['cp','-a','--reflink=never','--',case/'initial',run_root],90)
    flags=['--portable-terminal-probe','--cache-parent-fds']
    cmd=[sys.executable,qual,'--case',case,'--binary',producer,'--root',run_root]
    phase='strict-full-replay'
    foreground('strict',[*cmd,'--out',out/'strict-proof','--prepared-root',*flags],220)
    result['readbacks']['strict']=json.loads((out/'strict-proof/verification.json').read_text())
    phase='writer-clean-unmount'
    stop_mount(True)
    phase='cold-mount'
    visit('cold',ws,data,rurl,namespace)
    phase='cold-readback'
    foreground('cold',[*cmd,'--out',out/'cold-proof','--verify-only','--replay-result',out/'strict-proof/replay.json',*flags],90)
    result['readbacks']['cold']=json.loads((out/'cold-proof/verification.json').read_text())
    phase='cold-clean-unmount'
    stop_mount(True)
    result['passed']=True
''' + source[end:]
exec(compile(source,str(fixture),'exec'))

