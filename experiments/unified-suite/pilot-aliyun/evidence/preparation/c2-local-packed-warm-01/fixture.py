"""Repeat the two-file Packed fixture, measuring lower read activity only."""
import argparse
import hashlib
from pathlib import Path
import re
import sys

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('result',type=Path)
group = parser.add_mutually_exclusive_group()
group.add_argument('--native-stats',action='store_true')
group.add_argument('--candidate-binary',type=Path)
parser.add_argument('--candidate-sha256')
parser.add_argument('--retained-bytes',type=int)
args = parser.parse_args()
upstream_binary_sha256 = '814499855a949cad7a8752c04f272fa1b02524b36b7ea77b0e4b3bcc6031c5c0'
if args.candidate_binary is not None:
    if (not args.candidate_binary.is_file() or args.candidate_binary.resolve(strict=True) != args.candidate_binary
            or not re.fullmatch('[a-f0-9]{64}',args.candidate_sha256 or '')
            or args.candidate_sha256 == upstream_binary_sha256
            or args.retained_bytes is None or not 513 <= args.retained_bytes <= 64*1024**2):
        parser.error('candidate requires an absolute real binary, a distinct SHA-256 and a byte cap from 513 to 64 MiB')
    with args.candidate_binary.open('rb') as stream:
        if hashlib.file_digest(stream,'sha256').hexdigest() != args.candidate_sha256:
            parser.error('candidate binary SHA-256 differs')
elif args.candidate_sha256 is not None or args.retained_bytes is not None:
    parser.error('candidate SHA-256 and retained bytes require --candidate-binary')

here = Path(__file__).resolve()
source = here.parent.parent/'c4-workspace-v1-packed-fork-01/smoke.py'
raw = source.read_bytes()
if hashlib.sha256(raw).hexdigest() != '5e0cb515cb07851e2f8713ce78395fbf2b47796ad7a7c534eaea828ecd066e2d':
    raise ValueError('Frozen local fixture source changed')
text = raw.decode()
text = text.replace('result_path = Path(sys.argv[1])','result_path = options.result',1)
text = text.replace("AWS_EC2_METADATA_DISABLED='true', RUST_LOG=", "AWS_EC2_METADATA_DISABLED='true', BREWFS_FUSE_READ_DIRECT_IO='1', RUST_LOG=",1)
text = text.replace('c4-local-brewfs-packed-fork-smoke-v1','c2-local-brewfs-packed-warm-fixture-v1',1)
text = text.replace('tiny nonempty packed-v3 lower, no performance timing.',
                    'two passes through a framed Packed lower; no performance timing.',1)
start = text.index("    py = 'from pathlib import Path")
end = text.index('\n\ntry:',start)
text = text[:start]+"""    from loopback_probe import probe
    rows = [dict(path=k, offset=0, length=v['bytes'], sha256=v['sha256']) for k,v in expected.items()]
    result['lower_warmth'] = probe(point,rows,s3_snapshot,result_path.with_suffix('.lower'),
                                  retained_bytes=options.retained_bytes)
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
if args.native_stats:
    text = text.replace('from loopback_probe import probe','from bench.warm_lower import probe',1)
    text = text.replace('probe(point,rows,s3_snapshot,','probe(point,rows,',1)
    text = text.replace(",\n                                  retained_bytes=options.retained_bytes",'',1)
    text = text.replace("result['lower_warmth']['hot']","result['lower_warmth']['passed']",1)
    text = text.replace('metered_data,rurl,namespace','data,rurl,namespace',1)
elif args.candidate_binary is not None:
    text = text.replace("binary = binary_root / 'brewfs'","binary = options.candidate_binary",1)
    text = text.replace(f"binary: '{upstream_binary_sha256}'",'binary: options.candidate_sha256',1)
    text = text.replace("point = work/'mount'",'''env['BREWFS_PACKED_VERIFY_MEMO'] = '0'
env['BREWFS_PACKED_V3_RETAINED_PAYLOAD_BYTES'] = str(options.retained_bytes)
point = work/'mount' ''',1)
    text = text.replace('deadline = time.monotonic() + 138', '''result.update(candidate_binary=True,retained_payload_cap_bytes=options.retained_bytes,
              strict_frame_hash=True)
deadline = time.monotonic() + 138''',1)
sys.path.insert(0,str(here.parents[6]))
exec(compile(text,str(source),'exec'),{'__name__':'__main__','options':args})
