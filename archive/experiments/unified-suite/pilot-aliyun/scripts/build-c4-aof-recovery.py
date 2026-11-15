#!/usr/bin/env python3
"""Bind the C4 AOF allocation proof to the original failed capacity window."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


def require(ok, message):
    if not ok:
        raise ValueError(message)


def ref(path):
    path = Path(path).resolve(strict=True)
    return dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def load(path):
    return json.loads(Path(path).read_bytes())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('before','after','original-capacity','journal-evidence','verification','verifier',
                 'redis-audit','controller-audit','out'):
        parser.add_argument('--'+name,type=Path,required=True)
    a = parser.parse_args()
    require(not a.out.exists(),'Preserve all original evidence')
    before,after,old,v,audit,controller = (load(p) for p in
        (a.before,a.after,a.original_capacity,a.verification,a.redis_audit,a.controller_audit))
    require(before['cell_id']=='c5e97774bcffa69a-r1-native' and before['files']==100000 and
            before['pool']==after['pool']=='pool-a' and old['passed'] is False and
            old['before']==ref(a.before) and old['after']==ref(a.after), 'Wrong original failed capacity')
    require(v['schema']=='c4-aof-journal-verification-v1' and v['passed'] is True,
            'An independently verified journal is required')
    # The verifier receipt includes both the exact raw input and its own code.
    sources=v['source_pins']
    require(ref(a.journal_evidence) in sources and ref(a.verifier) in sources,
            'Verification is not bound to raw journal and verifier code')
    for p in sources:
        require(ref(p['path'])==p,'Changed verification source')
    journal=load(a.journal_evidence)
    meta=before['nodes']['metadata']
    require(journal['boot_id']==audit['boot']==meta['boot_id']==after['nodes']['metadata']['boot_id'] and
            journal['disk_serial']==meta['disks'][0]['serial'], 'Journal belongs to another host or disk')
    require(audit['config']['appendonly']=='yes' and audit['config']['appendfsync']=='always' and
            audit['config']['save']=='' and audit['config']['no-appendfsync-on-rewrite']=='no' and
            audit['config']['dir']=='/srv/ax-metadata/redis' and audit['config']['appenddirname']=='appendonlydir',
            'Redis persistence configuration changed')
    info=audit['info']
    require(int(info['process_id'])==meta['redis']['process_id']==after['nodes']['metadata']['redis']['process_id'] and
            info['role']=='master' and info['rdb_saves']=='0' and info['aof_rewrites_consecutive_failures']=='0' and
            info['aof_last_bgrewrite_status']==info['aof_last_write_status']=='ok', 'Unexplained Redis persistence activity')
    messages=[x['MESSAGE'] for x in audit['journal']]
    def exactly(s):
        require(sum(s in x for x in messages)==1,'Expected one successful rewrite event: '+s)
    for token in ('Starting automatic rewriting of AOF', 'Background append only file rewriting started by pid 65993',
                  'Successfully created the temporary AOF base file temp-rewriteaof-bg-65993.aof',
                  'Successfully renamed the temporary AOF base file temp-rewriteaof-bg-65993.aof into appendonly.aof.17.base.rdb',
                  'Background AOF rewrite terminated with success', 'Background AOF rewrite finished successfully'):
        exactly(token)
    require(not any(any(t in x.lower() for t in ('failed','error','truncate','terminated by signal')) for x in messages),
            'Unclosed Redis write/rewrite failure')
    for x in audit['journal']:
        when=int(x['__REALTIME_TIMESTAMP'])/1e6
        require(datetime.fromisoformat(before['at']).timestamp()<when<datetime.fromisoformat(after['at']).timestamp(),
                'Rewrite event outside original observations')
    dirs=audit['dirs']
    require([x['path'] for x in dirs['/srv/ax-metadata/redis']]==['/srv/ax-metadata/redis/appendonlydir'],
            'Unclassified Redis temporary file')
    living={Path(x['path']).name:x for x in dirs['/srv/ax-metadata/redis/appendonlydir']}
    require(set(living)=={'appendonly.aof.17.base.rdb','appendonly.aof.17.incr.aof','appendonly.aof.manifest'} and
            living['appendonly.aof.17.base.rdb']['inode']==524294 and
            'Generation: 2262346309' in audit['redis_tree'], 'Rewritten base did not survive the rename')
    original_sources=controller['sources']
    for item in original_sources:
        require(ref(item['path'])=={k:item[k] for k in ('path','sha256')} and load(item['path'])==item['value'],
                'Changed original controller evidence')
    runner=controller['runner']
    require(ref(runner['path'])=={k:runner[k] for k in ('path','sha256')} and
            hashlib.sha256(runner['source'].encode()).hexdigest()==runner['sha256'], 'Original controller source changed')
    source_pins=[ref(getattr(a,k)) for k in ('before','after','original_capacity','journal_evidence','verification',
        'verifier','redis_audit','controller_audit')]+[ref(__file__)]
    result=dict(schema='c4-aof-recovery-proof-v1',passed=True,at=datetime.now(timezone.utc).isoformat(),
        cell_id=before['cell_id'],pool='pool-a',before=ref(a.before),after=ref(a.after),
        original_capacity=ref(a.original_capacity),journal_evidence=ref(a.journal_evidence),
        verifier=ref(a.verifier),journal_verification=ref(a.verification),source_pins=source_pins,
        retirement_bytes=v['retirement_bytes'],retirement_inodes=v['retirement_inodes'],
        retired_aof_logical_bytes=v['retired_aof_logical_bytes'],
        scope='Original isolated C4 preparation forecast. Retired AOF and three manifest generations verified from journal; successful rewrite temp is the surviving .17 base. Entire before-used allocation is additionally reserved for any unidentified pre-existing reclamation. No performance admission.')
    with a.out.open('x') as f:
        json.dump(result,f,indent=2);f.write('\n')
    print(json.dumps(dict(passed=True,path=str(a.out),sha256=ref(a.out)['sha256'])))


if __name__=='__main__':
    main()
