"""Repeat the two-file Packed fixture, measuring lower read activity only."""
import hashlib
from pathlib import Path
import sys

here = Path(__file__).resolve()
source = here.parent.parent/'c4-workspace-v1-packed-fork-01/smoke.py'
raw = source.read_bytes()
if hashlib.sha256(raw).hexdigest() != '5e0cb515cb07851e2f8713ce78395fbf2b47796ad7a7c534eaea828ecd066e2d':
    raise ValueError('Frozen local fixture source changed')
text = raw.decode()
text = text.replace("AWS_EC2_METADATA_DISABLED='true', RUST_LOG=", "AWS_EC2_METADATA_DISABLED='true', BREWFS_FUSE_READ_DIRECT_IO='1', RUST_LOG=",1)
text = text.replace('c4-local-brewfs-packed-fork-smoke-v1','c2-local-brewfs-packed-warm-fixture-v1',1)
text = text.replace('tiny nonempty packed-v3 lower, no performance timing.',
                    'two passes through a framed Packed lower; no performance timing.',1)
start = text.index("    py = 'from pathlib import Path")
end = text.index('\n\ntry:',start)
text = text[:start]+"""    from loopback_probe import probe
    rows = [dict(path=k, offset=0, length=v['bytes'], sha256=v['sha256']) for k,v in expected.items()]
    result['lower_warmth'] = probe(point,rows,s3_snapshot,result_path.with_suffix('.lower'))
    stop_mount(True)
    return True
"""+text[end:]
old = "    if not visit('parent',parent['workspace_id'],data,rurl,namespace):\n        raise ValueError('Parent did not expose the expected two files')\n"
assert old in text
text = text.replace(old,"""    from loopback_probe import meter
    with meter(sp) as (observed_endpoint,s3_snapshot):
        metered_data = dict(data,s3=dict(data['s3'],endpoint=observed_endpoint))
        if not visit('parent',parent['workspace_id'],metered_data,rurl,namespace):
            raise ValueError('Parent did not expose the expected two files')
""",1)
start = text.index("    phase='fork-exact-revision'")
end = text.index('\nexcept Exception as error:',start)
text = text[:start]+"""    result['passed'] = result['lower_warmth']['hot']
"""+text[end:]
if '--native-stats' in sys.argv[2:]:
    text = text.replace('from loopback_probe import probe','from bench.warm_lower import probe',1)
    text = text.replace('probe(point,rows,s3_snapshot,','probe(point,rows,',1)
    text = text.replace("result['lower_warmth']['hot']","result['lower_warmth']['passed']",1)
    text = text.replace('metered_data,rurl,namespace','data,rurl,namespace',1)
sys.path.insert(0,str(here.parents[6]))
exec(compile(text,str(source),'exec'),{'__name__':'__main__'})
