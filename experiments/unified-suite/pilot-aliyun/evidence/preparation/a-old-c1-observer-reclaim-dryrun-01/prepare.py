"""Prepare one exact A C1 observer dry-run from a fresh original C4 boundary.

No deletion, service changes, model requests, archive creation or B-node access.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import runpy
import shlex
import subprocess

PILOT = Path('/srv/ax-pilot-control')
HERE = PILOT/'preparation/a-old-c1-observer-reclaim-01'
CANONICAL = PILOT/'parallel-control-01'
ORIGINAL = PILOT/'c123-control-01'
FORK = PILOT/'fork-control-01'
GUARD = PILOT/'preparation/c123-old-c1-retention-guard-02/runner.py'
GUARD_SHA = '7f1ff82922708cdea09da9ffc1930e94f60de1779ae9ae86e89db862cf1ea051'
TOOL_SHA = '022b0a101de5f9783e154ac5e6795595ed12fc10aa8bdfdcddcc5651f655b76a'
UNIT = 'ax-pilot-pa-c4-flat-reuse-01.service'
INVOCATION = 'd944eeaa8da34cc495ad8a993adade7f'
CELL = '7084af367e0bcfad-r1-native'
TAGS = ('c1-'+CELL, 'cal-n-rw-on1', 'cal-n-rw-on2', 'cal-n-rw-on3')
IDENTITIES = dict(client='20b62a11d155c59d6abf3bb0b26d9f64234bd27e9c557447aa476bf695f33651',
                  metadata='122e570ef3ea8192409d5ab879252b7c2c1a256b40266f06baac5dad72d9625d',
                  objects='277e3ff412b8b4d22f8b3329614aa094031458ec8ae0b9bb32b9d76b90257f92')


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def ref(path, expected=None):
    require(path.resolve(strict=True) == path and path.is_file(), 'Original regular source required')
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    require(expected is None or digest == expected, 'Changed source: '+str(path))
    return dict(path=str(path), sha256=digest)


def read(path):
    require(path.stat().st_size <= 16*1024**2, 'Only bounded metadata inputs')
    return json.loads(path.read_bytes())


def write(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write('\n')


tool_path = HERE/'reclaim-closed-object-observer.py'
tool_ref = ref(tool_path, TOOL_SHA)
guard_ref = ref(GUARD, GUARD_SHA)
guard = runpy.run_path(str(GUARD))
tool = runpy.run_path(str(tool_path))
guard_stage = PILOT/'preparation/c123-old-c1-retention-guard-02/stage-check.json'
staged = read(guard_stage)
s = dict(schema='c1-closed-object-observer-reclaim-v1', reviewed=True,
         tool=tool_ref, guard=guard_ref, guard_stage=ref(guard_stage),
         canonical_plan=ref(CANONICAL/'suite/plan.json'),
         lock_plan=ref(FORK/'suite/plan.json'), original_plan=ref(ORIGINAL/'suite/plan.json'),
         lock_cell='c5e97774bcffa69a-r1-native', fleet=ref(CANONICAL/'fleet.json'),
         ssh_config=ref(CANONICAL/'ssh.conf'),
         lease_controls=sorted(str(path.parent) for path in PILOT.glob('*/state.sqlite')),
         resident_units=staged['resident_units'],
         worker_identities={role: ref(CANONICAL/'hardware-qualification-02'/('pool-a-'+role+'.json'), digest)
                            for role, digest in IDENTITIES.items()})
plan, cell, eligible = tool['lock_context'](s)
require(CELL in eligible, 'Only original A C1 may be selected')
guard['identities'](s, cell, read(CANONICAL/'fleet.json'))
out = HERE/'boundary-01'
out.mkdir(mode=0o700)
terminal_path = PILOT/'preparation/a-c4-parent-close-01/terminal-observation-01.json'
terminal_ref = ref(terminal_path, '4c5d4689666197f422eede0bbc1d722ad4cf8d06fa0c87ad2b0a3bb513cdad61')
terminal = read(terminal_path)
off1 = FORK/'calibration/flat-off1'
finished_ref = ref(off1/'finished.json', 'e02cb37af444573cc20ba9563736a0c162c72029cc0c2508991ce814c52bce64')
finished = read(off1/'finished.json')
closure_ref = ref(off1/'closure.json', '0fa0a0190efab41ccad72f0128d14ce46beffa2214516c6533497e6f8c50fca9')
closure = read(off1/'closure.json')
require(finished['passed'] is True and finished['closure'] == closure_ref and closure['passed'] is True
        and closure['all_exited'] is True and closure['all_unmounted'] is True
        and closure['pending_upload_bytes'] == closure['pending_upload_operations'] == 0,
        'Original Off1 must have fully closed with zero debt')
require(read(FORK/'batch-a-flat-reuse-01/failed.json') == terminal['results']['failed.json']
        and read(FORK/'batch-a-flat-reuse-01/trial-1/exit.json') == {'exit_code': 0}
        and not any(terminal['pids_present'].values()) and terminal['trial2_empty'] is True,
        'Original terminal EEXIST stop differs')
batch_path = PILOT/'preparation/c4-aof-reconcile-01/batch.request.json'
ref(batch_path, 'bd28482c6bb3d46c288e791e11f3d8aee1462b00ec43516cc523496ccdcf9392')
batch = read(batch_path)
batch_tool = PILOT/'code-19/experiments/unified-suite/pilot-aliyun/scripts/run-c4-batch-pretrial-01.py'
batch_tool_ref = ref(batch_tool, 'aefb4ad8763f46cb8a4cbd07669818a568019642814f2de3ca8d7d46483eacf6')
batch_module = runpy.run_path(str(batch_tool))
runtime = FORK/'runtime-a-03'
runtime_fleet = read(runtime/'fleet.json')
attempts = [t['attempt'] for t in batch['trials']]+[batch['formal_attempt']]
write(out/'intent.json', dict(preparer=ref(Path(__file__)), targets=list(TAGS), automatic_retry=False,
                              locks=guard['lock_resources'](plan), action='read-only boundary and exact request'))
with guard['two_pool_guard'](CANONICAL/'runner-locks', guard['lock_resources'](plan)):
    guard['require_no_leases'](s)
    guard['services_idle'](s)
    manager = guard['show_unit'](UNIT, guard['MANAGER_FIELDS'])
    require(manager == dict(LoadState='loaded', InvocationID=INVOCATION, ActiveState='failed',
                           SubState='failed', MainPID='0', ControlPID='0', Result='exit-code', ExecMainStatus='1'),
            'Original A parent must remain at its terminal EEXIST boundary')
    live = {}
    for role, root in guard['ROLE_ROOTS'].items():
        node = next(n for n in runtime_fleet['nodes'] if n['id'] == 'pool-a-'+role)
        code = batch_module['failure_release_code'](root, node['repo'], role, batch['asset'], attempts)
        script = out/(role+'-probe.py')
        script.write_text(code)
        call = subprocess.run(['ssh', '-F', str(CANONICAL/'ssh.conf'), *guard['SSH_OPTIONS'],
                               'pool-a-'+role, shlex.join(['python3', '-B', '-'])],
                              input=code.encode(), capture_output=True, timeout=30)
        (out/(role+'-stdout')).write_bytes(call.stdout)
        (out/(role+'-stderr')).write_bytes(call.stderr)
        require(call.returncode == 0, 'Original '+role+' read-only release probe failed')
        result = json.loads(call.stdout)
        require(result['safe_for_release'] is True, 'Original '+role+' is not quiescent')
        live[role] = dict(script=ref(script), result=ref(out/(role+'-stdout')))
    capacity_code = """import json,os,pathlib
p=pathlib.Path('/srv/ax-objects-0');v=os.statvfs(p)
tags=%r
print(json.dumps(dict(total_bytes=v.f_blocks*v.f_frsize,free_bytes=v.f_bavail*v.f_frsize,
                     free_inodes=v.f_favail,device=p.stat().st_dev,
                     roots={tag:dict(device=(q:=p/'o'/tag).lstat().st_dev,inode=q.lstat().st_ino,
                                     mtime_ns=q.lstat().st_mtime_ns) for tag in tags})))
""" % (TAGS,)
    (out/'objects-small-stat.py').write_text(capacity_code)
    stat_call = subprocess.run(['ssh', '-F', str(CANONICAL/'ssh.conf'), *guard['SSH_OPTIONS'],
                               'pool-a-objects', shlex.join(['python3', '-B', '-'])],
                              input=capacity_code.encode(), capture_output=True, timeout=30)
    require(stat_call.returncode == 0, 'Exact object root stat failed')
    (out/'objects-small-stat.json').write_bytes(stat_call.stdout)
    capacity = json.loads(stat_call.stdout)
    s['minimum_after_bytes'] = (capacity['total_bytes']+4)//5
    s['space_floor_scope'] = 'Twenty percent only; separate next-round peak capacity admission remains required'
    require(guard['show_unit'](UNIT, guard['MANAGER_FIELDS']) == manager, 'Original manager changed during probes')
    write(out/'live-check.json', dict(passed=True, probes=live, objects_small_stat=ref(out/'objects-small-stat.json'),
          original_batch_tool=batch_tool_ref, original_finished=finished_ref, original_closure=closure_ref))
    proof = dict(schema='c123-pool-a-retention-quiescence-v1', passed=True, reviewed=True, pool='pool-a',
                 observed_at=datetime.now(timezone.utc).isoformat(), unit=UNIT, manager=manager,
                 resource_ids=cell['resource_ids'], no_active_pool_lease=True, agent_slot_empty=True,
                 all_workload_processes_exited=True, all_mounts_absent=True,
                 pending_upload_bytes=0, pending_upload_operations=0,
                 source_pins=[terminal_ref, finished_ref, closure_ref, ref(out/'live-check.json')])
    write(out/'quiescence.json', proof)
    s['quiescence'] = ref(out/'quiescence.json')
    guard['boundary'](s, cell)
    formal = read(HERE/'public-formal.json')
    calibration = read(HERE/'public-calibrations.json')
    targets = []
    for tag in TAGS:
        is_formal = tag.startswith('c1-')
        base = ORIGINAL/'cells'/CELL/'archives' if is_formal else ORIGINAL/'retained-calibrations/native-randwrite-02'/tag
        check_path = base/('objects-check.json' if is_formal else 'objects-archive-check.json')
        checked = read(check_path)
        published = formal if is_formal else calibration
        item = dict(tag=tag, cell_id=CELL, role_archive=dict(path=str(base/'objects.tar.gz'), sha256=checked['sha256']),
                    role_check=ref(check_path), public_archive={k: published['prepared'][k] for k in ('sha256',)},
                    public_receipt=ref(HERE/('public-formal.json' if is_formal else 'public-calibrations.json')))
        item['public_archive']['path'] = published['prepared']['source']
        if not is_formal:
            item.update(public_member=tag+'/objects.tar.gz',
                        public_check=ref(ORIGINAL/'retained-calibrations/native-randwrite-02-archive-check.json'))
        targets.append(item)
    s['targets'] = targets
    write(HERE/'request.reviewed.json', s)
    write(HERE/'prepared.json', dict(passed=True, request=ref(HERE/'request.reviewed.json'), tool=tool_ref,
          targets=list(TAGS), minimum_after_bytes=s['minimum_after_bytes'],
          initial_free_bytes=capacity['free_bytes'], guard_released_on_exit=True, applied=False,
          scope='Fresh read-only boundary; archived and live file bytes will be checked by the original dry-run path'))
print(json.dumps(ref(HERE/'request.reviewed.json')))
