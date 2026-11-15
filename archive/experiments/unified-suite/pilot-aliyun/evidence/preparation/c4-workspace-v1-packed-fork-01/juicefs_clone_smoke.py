"""Qualify native JuiceFS clone with the same two-file C4 source as BrewFS."""
import hashlib
from pathlib import Path
import sys

source = Path(__file__).resolve().with_name('smoke.py')
raw = source.read_bytes()
if hashlib.sha256(raw).hexdigest() != '5e0cb515cb07851e2f8713ce78395fbf2b47796ad7a7c534eaea828ecd066e2d':
    raise ValueError('Frozen C4 fixture source changed')
text = raw.decode()
text = text.replace("binary = binary_root / 'brewfs'","binary = binary_root / 'juicefs'",1)
text = text.replace(
    "    binary: '814499855a949cad7a8752c04f272fa1b02524b36b7ea77b0e4b3bcc6031c5c0',\n"
    "    producer: 'aa45b1ee9640da92051ead72b17ed41836b57812e05623f860b963e09373b3ab',",
    "    binary: '6d450d3c151b6affede364d9a198d2505111b0612a5fb4d31613444c635174cc',",1)
text = text.replace("ax-c4-packed-fork","ax-c4-jfs-clone").replace("ax.c4.packed-fork.owner","ax.c4.jfs-clone.owner")
text = text.replace('c4-local-brewfs-packed-fork-smoke-v1','c4-local-juicefs-native-clone-smoke-v1',1)
text = text.replace("    source_commit=", "    fixture_reference_brewfs_commit=",1)
text = text.replace('tiny nonempty packed-v3 lower','two-file native clone source',1)
text = text.replace("    phase='create-base-bucket'","""    phase='object-api-ready'
    from botocore.exceptions import ClientError
    def object_api_ready():
        try:
            return s3.list_buckets()['ResponseMetadata']['HTTPStatusCode']==200
        except ClientError as error:
            if error.response['ResponseMetadata']['HTTPStatusCode']==503:
                return False
            raise
    wait_for(object_api_ready,'RustFS signed object API',20)
    phase='create-base-bucket'""",1)
start = text.index("    phase='publish-fixture'")
end = text.index('\nexcept Exception as error:',start)
text = text[:start] + """    env.update(ACCESS_KEY=access,SECRET_KEY=secret,AWS_DEFAULT_REGION='us-east-1',
               no_proxy='127.0.0.1,localhost')
    phase='format-juicefs'
    result['juicefs_version']=invoke([binary,'--version'],limit=5).stdout.strip()
    invoke([binary,'format','--storage','s3','--bucket',endpoint+'/'+bucket,'--block-size','4M',
        '--compress','none','--trash-days','0',rurl,bucket],limit=20)

    def attach(side):
        global proc,log,active_side
        if mounted() or proc is not None:
            raise ValueError('Prior JuiceFS mount is still present')
        active_side = side
        log_file=work/(side+'.log')
        log=log_file.open('xb')
        proc=subprocess.Popen([binary,'mount',rurl,point,'--cache-dir',work/('cache-'+side),
            '--cache-size','0','--attr-cache','0','--entry-cache','0','--dir-entry-cache','0',
            '--prefetch','0','--no-usage-report=true'],stdout=log,stderr=subprocess.STDOUT,env=env)
        def ready():
            if proc.poll() is not None:
                raise ValueError('JuiceFS mount exited: '+clean_text(log_file.read_text(errors='replace')))
            return mounted()
        wait_for(ready,side+' FUSE mount',20)
        result.setdefault('mount_pids',{})[side]=proc.pid

    def compare(label,root):
        if set(str(path.relative_to(root)) for path in root.rglob('*')) != {'a.txt','sub','sub/b.txt'}:
            raise ValueError('Native clone changed the tree shape')
        actual={}
        for leaf,data_bytes in payload.items():
            path=root/leaf
            body=path.read_bytes()
            actual[leaf]=dict(bytes=len(body),sha256=hashlib.sha256(body).hexdigest())
            if body != data_bytes or path.stat().st_mode & 0o777 != 0o640:
                raise ValueError('Native clone changed bytes or file mode')
        if (root/'sub').stat().st_mode & 0o777 != 0o750:
            raise ValueError('Native clone changed directory mode')
        result['readbacks'][label]=dict(passed=actual==expected,files=actual)

    phase='seed-and-read-parent'
    attach('writer')
    origin,child=point/'parent',point/'child'
    origin.mkdir()
    (origin/'sub').mkdir()
    (origin/'sub').chmod(0o750)
    for leaf,data_bytes in payload.items():
        with (origin/leaf).open('xb') as stream:
            os.fchmod(stream.fileno(),0o640)
            stream.write(data_bytes)
            stream.flush()
            os.fsync(stream.fileno())
    compare('parent',origin)
    phase='native-clone'
    invoke([binary,'clone','-p','--threads','4',origin,child],limit=20)
    result['clone_exit']=0
    compare('child',child)
    phase='isolation'
    changed=child/'a.txt'
    with changed.open('wb') as stream:
        stream.write(b'isolated child')
        stream.flush()
        os.fsync(stream.fileno())
    compare('parent-after-child-write',origin)
    stop_mount(True)
    phase='cold-read'
    attach('cold')
    if result['mount_pids']['writer']==result['mount_pids']['cold']:
        raise ValueError('Cold mount reused the previous process')
    compare('cold-parent',origin)
    if changed.read_bytes()!=b'isolated child' or (child/'sub/b.txt').read_bytes()!=payload['sub/b.txt']:
        raise ValueError('Native clone child changes failed cold readback')
    result['cold_child_isolated']=True
    stop_mount(True)
    result['passed']=True
""" + text[end:]
exec(compile(text,str(source),'exec'),{'__name__':'__main__'})
