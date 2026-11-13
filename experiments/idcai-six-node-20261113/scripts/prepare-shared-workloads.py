"""Adapt the pinned AX lifecycle runners to this campaign's shared backend.

The resulting runners are qualification only. The shared services belong to the
coordinator and are deliberately not stopped by an individual arm.
"""
from pathlib import Path
import sys

ROOT = Path('/srv/ax-idcai-20261113')
REVISION = '86f704be9129fbb5f6e466cfbbb15fdf383fc4e8'


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'unexpected pinned source at {old!r}')
    return text.replace(old, new, 1)


def backend(text):
    text = once(text, 'a = p.parse_args()', '''p.add_argument('--redis-db', type=int, required=True)
a = p.parse_args()
if not 0 <= a.redis_db < 256:
    p.error('exclusive Redis DB required')
backend = json.load(sys.stdin)
if set(backend) != {'key', 'secret', 'rpc'}:
    p.error('private backend credentials required on stdin')
shared_endpoint = 'http://10.77.13.21:19000'
''')
    text = once(text, 'key, secret = secrets.token_hex(12), secrets.token_hex(24)',
                "key, secret, rpc = (backend[k] for k in ('key', 'secret', 'rpc'))")
    text = once(text, '(key, secret))', '(key, secret, rpc))')
    text = text.replace("NO_PROXY='127.0.0.1,localhost'", "NO_PROXY='127.0.0.1,localhost,10.77.13.21,10.77.13.24'")
    text = text.replace(".replace(key,'[redacted]')", ".replace(key,'[redacted]').replace(rpc,'[redacted]')")
    start = text.index('    rp = local_port()')
    stop = text.index('    s3 = client(', start)
    text = text[:start] + '''    ep = shared_endpoint
    result['shared_backend'] = {'s3':ep, 'redis':'10.77.13.24:16379', 'database':a.redis_db}
''' + text[stop:]
    text = once(text, "    result['rustfs_image'] = call('docker','image','inspect','-f','{{.Id}}',a.rustfs_image).stdout.strip()",
                "    result['rustfs_image'] = a.rustfs_image  # coordinator already checks every member")
    text = once(text, '[(a.product, 0)]', '[(a.product, a.redis_db)]')
    text = once(text, "url = f'redis://127.0.0.1:{rp}/{db}'", "url = f'redis://:{rpc}@10.77.13.24:16379/{db}'")
    text = once(text, "s3={'addressing_style':'path'}))", "s3={'addressing_style':'path'},proxies={}))")
    text = once(text, '    result[\'cleanup\'] = closed', "    closed['shared_backend_managed_by_coordinator'] = True\n    result['cleanup'] = closed")
    return text


def lifecycle(text):
    text = backend(text)
    text = once(text, "result['smallfiles_options'] = opts", '''fixture = Path('/srv/ax-idcai-20261113/input/pilot-100k-001')
if a.workload == 'c3':
    if a.tiny:
        p.error('100k qualification cannot use --tiny')
    spec = json.loads((fixture/'input-set.json').read_text())
    if not spec['passed'] or spec['files_per_tree'] != 100000:
        raise ValueError('unqualified C3 fixture')
    manifest_path = fixture/spec['c3']['manifest']['path']
    manifest = json.loads(manifest_path.read_text())
    if hashlib.sha256(manifest_path.read_bytes()).hexdigest() != spec['c3']['manifest']['sha256']:
        raise ValueError('C3 manifest digest differs')
    opts = ['--files','100000','--hot-files','1024','--workers','8','--read-ops','8192',
            '--cow-files','256','--write-ops','256','--mixed-ops','1024','--seed','9173','--workspace','1']
    result['pilot_input_manifest_sha256'] = spec['c3']['manifest']['sha256']
    packed_diagnostic = os.environ.get('AX_IDCAI_C3_PACKED_BUDGET_DIAGNOSTIC') == '1'
    if packed_diagnostic:
        if a.product != 'brewfs' or a.engine != 'workspace-v1':
            raise ValueError('Packed budget diagnostic is only for C3 BrewFS Workspace')
        result['packed_budget_diagnostic'] = {'output_bytes': 128 * 2**20, 'control_bytes': 8 * 2**20}
result['smallfiles_options'] = opts''')
    text = once(text, "    call(a.smallfiles, stage, '--root', root, '--output', out, *opts, *extra)", '''    source_path = fixture/'smallfiles-source'
    if stage == 'prepare':
        source = source_path
        if file_tree(source) != manifest:
            raise ValueError('C3 source differs from frozen manifest')
        root.symlink_to(source, target_is_directory=True)
        root = source
        stage = 'verify-base'
    if stage in ('verify-base','verify-touched'):
        out.mkdir()
        expected_state = out.parent/'run/expected-touched.tsv' if stage == 'verify-touched' else 'base'
        verifier = 'smallfiles-verify-juicefs' if a.product == 'juicefs' and root != source_path else 'smallfiles-verify'
        call(Path('/srv/ax-idcai-20261113/bin')/verifier, root, expected_state,
             out/'result.json','100000','16','1',timeout=a.prepare_timeout)
    else:
        call(a.smallfiles, stage, '--root', root, '--output', out, *opts, *extra,
             timeout=a.prepare_timeout)''')
    text = once(text, "'--source-directory',work/'source'", "'--source-directory',(work/'source').resolve()")
    text = once(text, '                    call(*producer_cmd)', '                    call(*producer_cmd,timeout=a.prepare_timeout)')
    text = once(text, "                raw = call(*init_cmd).stdout", "                raw = call(*init_cmd,timeout=a.prepare_timeout).stdout")
    text = once(text, "            stats('before')", "            stats('before')\n            capture_native(label+'-before',point,out,product)")
    text = once(text, "            stats('after')", "            stats('after')\n            capture_native(label+'-after',point,out,product)")
    text = once(text, "    with (out/(label+'.log')).open('w') as log:", '''    if 'packed_budget_diagnostic' in result:
        settings = result['packed_budget_diagnostic']
        mount_env.update(BREWFS_PACKED_V3_OUTPUT_BUDGET_BYTES=str(settings['output_bytes']),
                         BREWFS_PACKED_V3_CONTROL_BUDGET_BYTES=str(settings['control_bytes']),
                         RUST_LOG='error,brewfs::packed_v3_budget=debug')
    with (out/(label+'.log')).open('w') as log:''')
    text = once(text, "                        shutil.copytree(work/'source'/'small',point/'small')", '''                        with journal.stage('materialize-c3'):
                            incoming = point/'c3-incoming'
                            call(Path('/srv/ax-idcai-20261113/bin/smallfiles-copy'),
                                 (work/'source').resolve(), incoming, out/'import.json',
                                 '100000','16',timeout=a.prepare_timeout)
                            (incoming/'small').rename(point/'small')
                            incoming.rmdir()''')
    text = once(text, '                from bench.runtime.move_client import reopen',
                '                from idcai_move_client import reopen')
    text = once(text, "            (out/'closed-tree.json').write_text", "            from bench.runtime.tree_verify import write_probe\n            proof['write_probe'] = write_probe(workspace)\n            (out/'closed-tree.json').write_text")
    text = once(text, "def visit(product,", '''def capture_native(label, point, out, product):
    if product == 'brewfs':
        data = (point/'.stats').read_bytes()
    else:
        from urllib.request import ProxyHandler, build_opener
        with build_opener(ProxyHandler({})).open('http://127.0.0.1:18910/metrics', timeout=4) as response:
            data = response.read()
    if not data:
        raise ValueError('empty client metrics')
    (out/(label+'.prom')).write_bytes(data)


def visit(product,''')
    text = once(text, "                            '--cache-dir',work/cache,url,point]", "                            '--cache-dir=memory','--cache-size=512','--buffer-size=512','--attr-cache=0',\n                            '--entry-cache=0','--dir-entry-cache=0','--open-cache=0','--prefetch=1',\n                            '--max-readahead=128','--cache-large-write=true','--verify-cache-checksum=full',\n                            '--backup-meta=0','--no-usage-report','--metrics=127.0.0.1:18910',url,point]")
    text = once(text, "                return mounted(point)", "                if not mounted(point):\n                    return False\n                if product == 'juicefs':\n                    with socket.socket() as listener:\n                        listener.settimeout(.3)\n                        return listener.connect_ex(('127.0.0.1',18910)) == 0\n                return True")
    return text


def native_clone(text):
    text = backend(text)
    a = text.index("    source = work/'source'")
    b = text.index("    expected = result['source']", a)
    text = text[:a] + '''    fixture = Path('/srv/ax-idcai-20261113/input/pilot-100k-001')
    spec = json.loads((fixture/'input-set.json').read_text())
    source = fixture/spec['c4']['root']
    manifest = fixture/spec['c4']['manifest']['path']
    if not spec['passed'] or spec['files_per_tree'] != 100000 or hashlib.sha256(manifest.read_bytes()).hexdigest() != spec['c4']['manifest']['sha256'] or file_tree(source) != json.loads(manifest.read_text()):
        raise ValueError('unqualified C4 fork input')
    result['files'] = 100000
    result['pilot_input_manifest_sha256'] = spec['c4']['manifest']['sha256']
''' + text[b:]
    text = once(text, "            changed = {'base.txt':", "            changed = {min(expected):")
    text = once(text, "with journal.stage(label+'-check', timeout=90):", "with journal.stage(label+'-check', timeout=900):")
    text = once(text, "call(a.juicefs,'clone',parent_path,child_path)", "call(a.juicefs,'clone',parent_path,child_path,timeout=300)")
    text = once(text, "'--s3-rustfs-ec-block-size-hint','auto')", "'--s3-rustfs-ec-block-size-hint','auto',timeout=900)")
    text = once(text, "'--next-head',uuid.uuid4()).stdout,'publication')", "'--next-head',uuid.uuid4(),timeout=900).stdout,'publication')")
    text = once(text, "'--count','1','--data-config',cfg).stdout,'child')", "'--count','1','--data-config',cfg,timeout=300).stdout,'child')")
    text = once(text, "                    keys = call('redis-cli','-p',rp,'-n',db,'--scan','--pattern',", "                    keys = call('redis-cli','-u',url,'--scan','--pattern',")
    text = once(text, "    return {name: row['sha256'] for name, row in file_tree(root).items() if row['type']=='file'}", '''    from concurrent.futures import ThreadPoolExecutor
    def shard(directory):
        return {str(directory.relative_to(root)/name): row['sha256']
                for name,row in file_tree(directory).items() if row['type']=='file'}
    children = list(root.iterdir())
    expected = {x.name:hashlib.sha256(x.read_bytes()).hexdigest() for x in children if x.is_file()}
    with ThreadPoolExecutor(max_workers=16) as pool:
        for rows in pool.map(shard,(x for x in children if x.is_dir())):
            expected.update(rows)
    return expected''')
    text = once(text, '                    shutil.copytree(source,parent_path)', '''                    from concurrent.futures import ThreadPoolExecutor
                    parent_path.mkdir()
                    def copy_shard(path):
                        if not path.is_dir() or path.is_symlink():
                            raise ValueError('C4 frozen source must have directory shards')
                        shutil.copytree(path,parent_path/path.name)
                    with ThreadPoolExecutor(max_workers=16) as pool:
                        list(pool.map(copy_shard,sorted(source.iterdir())))''')
    return text


def main(repo):
    from subprocess import check_output
    if check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()!=REVISION:
        raise ValueError('wrong AX revision')
    scripts = repo/'experiments/unified-suite/pilot-aliyun/scripts'
    candidate = []
    for name, adapt in [('rbm-lifecycle.py', lifecycle), ('rbm-native-fork.py', native_clone)]:
        path = scripts/name
        result = adapt(path.read_text())
        compile(result, str(path), 'exec')
        candidate.append((path,result))

    move = Path(__file__).with_name('idcai_move_client.py').read_text()
    compile(move, 'idcai_move_client.py', 'exec')
    candidate.append((scripts/'idcai_move_client.py',move))
    for path,result in candidate:
        path.write_text(result)


if __name__ == '__main__':
    main(Path(sys.argv[1]))
