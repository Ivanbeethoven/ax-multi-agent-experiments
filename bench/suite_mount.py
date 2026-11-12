"""Draft explicit client settings and bind native mounts to a frozen suite cell.

This is configuration validation, not proof of effective cache behavior. Native
qualification and cache/IO counters are still required. No process is started.
"""
import argparse
from copy import deepcopy

from .experiment_io import load, write_new
from .suite_catalog import CLIENT_PROFILE

MIB = 1024**2


def settings(config, arm):
    if config.get('client_profile') != CLIENT_PROFILE:
        raise ValueError('explicit bounded-memory-v1 campaign required; keep old plans unchanged')
    budget = arm['payload_cache_bytes']
    if arm['product'] == 'juicefs':
        return {'cache_dir':'memory', 'cache_mib':budget//MIB, 'buffer_mib':512,
                'writeback':False, 'attr_cache_seconds':0, 'entry_cache_seconds':0,
                'open_cache_seconds':0, 'prefetch':1 if budget else 0,
                'max_readahead_mib':128, 'cache_large_write':True,
                'verify_cache_checksum':'full', 'backup_meta':'0', 'no_usage_report':True}
    # Frozen plans retain their recorded engine names when reconstructing hashes.
    entity = arm['engine'] in ('entity-workspace', 'workspace-entity-v1')
    value = {'volume_format':arm['engine'], 'volume':{'format':arm['engine']},
             'cache':{'read_memory_bytes':0 if entity else budget, 'read_ssd_bytes':0,
                      'write_memory_bytes':512*MIB, 'write_ssd_bytes':1024*MIB,
                      'memory_budget_bytes':512*MIB, 'writeback_mode':'upload_before_commit',
                      'compression':'none', 'verify_cache_checksum':'full',
                      'prefetch_enabled':True, 'prefetch_max_bytes':128*MIB,
                      'prefetch_concurrency':64, 'range_background_prefetch':True,
                      'read_fetch_policy':'backend-default',
                      'populate_write_cache_after_upload':True,
                      'persist_write_cache_after_upload':False},
             'fuse':{'cache_ttl_ms':0, 'keep_cache':False},
             'data':{'s3':{'rustfs_ec_block_size_hint':arm['hint']}}}
    if entity:
        # The entity adapter does not use the native VFS read cache or its
        # prefetch switches. Its packed cache is explicitly selected by CLI.
        value['entity_read_cache_bytes'] = budget
    if arm['engine'] in ('native-workspace', 'workspace-native-v2'):
        value['volume'].update(schema_version=2,native_control_version=2)
    if 'object_bytes' in arm:
        value['layout'] = {'block_size':arm['object_bytes']}
        value['volume']['block_size'] = arm['object_bytes']
    return value


def overlay(base, expected):
    result = deepcopy(base)
    for k,v in expected.items():
        result[k] = overlay(result.get(k,{}),v) if isinstance(v,dict) else v
    return result


def matches(actual, expected, prefix=''):
    for k,v in expected.items():
        name=prefix+k
        if k not in actual:raise ValueError('missing explicit client setting: '+name)
        if isinstance(v,dict):
            if not isinstance(actual[k],dict):raise ValueError('invalid client section: '+name)
            matches(actual[k],v,name+'.')
        elif type(actual[k]) is not type(v) or actual[k]!=v:
            raise ValueError('client setting differs from frozen cell: '+name)


def cell_of(plan, cell_id):
    from .suite_plan import check_plan
    check_plan(plan)
    cell=next((c for c in plan['cells'] if c['id']==cell_id),None)
    if cell is None:raise ValueError('unknown suite cell')
    return cell


def validate_binding(binding, native, filesystem, pool, metadata_backend, hint):
    from .runtime.common import pinned
    plan=load(pinned(binding['plan']));cell=cell_of(plan,binding['cell_id'])
    if (cell['arm']['product'],cell['pool'],plan['config']['metadata_backend']) != (filesystem,pool,metadata_backend):
        raise ValueError('mount route/pool/backend differs from frozen cell')
    if filesystem=='brewfs' and hint!=cell['arm']['hint']:
        raise ValueError('mount hint differs from frozen cell')
    if not cell.get('client_settings'):raise ValueError('mount requires a plan with explicit client settings')
    matches(native,cell['client_settings'])
    if filesystem=='brewfs':
        if 'root' in native['cache']:raise ValueError('use cache_root without an ambiguous root alias')
        if cell['arm']['engine']=='native-workspace':
            if not native.get('native_base') or any(type(native['volume'].get(k)) is not int or native['volume'][k]<=0
                                                  for k in ('chunk_size','block_size')):
                raise ValueError('native workspace requires qualified native_base and explicit volume geometry')
        elif native.get('native_base') is not None or any(k in native['volume'] for k in ('schema_version','native_control_version')):
            raise ValueError('native workspace controls cannot be reused on another engine')
        if cell['arm']['engine']=='entity-workspace':
            lower='packed_manifest_key' if cell['arm']['packed'] else 'entity_native_manifest_key'
            if not native.get(lower):raise ValueError('entity lower format differs from frozen arm')
    return cell


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('plan','cell','config','output'):p.add_argument('--'+key,required=True)
    a=p.parse_args();plan=load(a.plan);cell=cell_of(plan,a.cell)
    if not cell.get('client_settings'):raise ValueError('keep legacy plan unchanged; create a new configured campaign')
    write_new(a.output,overlay(load(a.config),cell['client_settings']))
