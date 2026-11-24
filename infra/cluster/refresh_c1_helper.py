"""Build and exercise the C1 reader before filesystem measurements."""
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0,'/srv/ax-cluster/direct/scripts')
import cluster_lib as cl


def main():
    output=cl.RUN_ROOT/'functional/c1-reader'
    output.mkdir(parents=True,exist_ok=True)
    binary=output/'c1-io-helper'
    source=cl.REPO_ROOT/'tools/fs-probe/src/bin/c1-io-helper.rs'
    compiler='/srv/ax-cluster-20261113/toolchain-1.99.0/bin/rustc'
    with cl.MeasurementLock():
        subprocess.run([compiler,'--edition=2021','-O','-Ccodegen-units=4',str(source),'-o',str(binary)],check=True)
        (output/'c1-io-helper.rs').write_bytes(source.read_bytes())
        root=output/'input';root.mkdir(exist_ok=True)
        common=['--root',str(root),'--jobs','8','--bytes-per-job',str(32*1024**2)]
        records={}
        for name,action in [('prepare','init-files'),('cold','layout-fuse-pass'),
                            ('hot','layout-fuse-pass'),('two-passes','layout-fuse')]:
            result=subprocess.run([str(binary),action,*common,'--out',str(output/(name+'.json'))],
                                  capture_output=True,text=True,check=True)
            records[name]=json.loads(result.stdout)
            if name!='prepare':
                doc=records[name]
                if not doc['passed'] or doc['operations']!=4096 or doc['application_read_bytes']!=16*1024**2:
                    raise RuntimeError('Reader operation count or content check failed')
        with (root/'job-0.dat').open('r+b') as stream:stream.write(bytes(4*1024**2))
        damaged=subprocess.run([str(binary),'layout-fuse-pass',*common],capture_output=True,text=True)
        if damaged.returncode==0 or 'content mismatch' not in damaged.stderr:
            raise RuntimeError('Reader did not reject changed file content')
        records['changed_content_rejected']=True
        (output/'changed-content.stderr').write_text(damaged.stderr)
        target=cl.BIN_DIR/'c1-io-helper'
        if target.is_symlink():target.unlink()
        target.write_bytes(binary.read_bytes());target.chmod(0o755)
        for host in cl.ALL_HOST_NAMES:
            if host=='node-coord':continue
            temporary=str(target)+'.new'
            cl.sync_file_to_host(host,binary,temporary)
            cl.run_on_host(host,['python3','-c',
                'from pathlib import Path; import sys; p=Path(sys.argv[1]); p.chmod(0o755); p.replace(sys.argv[2])',
                temporary,str(target)],log_cmd=False)
        records['binary_bytes']=binary.stat().st_size
        records['rustc']=subprocess.check_output([compiler,'--version'],text=True).strip()
        records['source']=str(source)
        records['hosts']=cl.ALL_HOST_NAMES
        (output/'validation.json').write_text(json.dumps(records,indent=2)+'\n')
        version_file=cl.RUN_ROOT/'versions.json'
        versions=json.loads(version_file.read_text())
        versions['c1_reader']={key:records[key] for key in ('binary_bytes','rustc','source','hosts')}
        versions['c1_reader']['validation']='functional/c1-reader/validation.json'
        version_file.write_text(json.dumps(versions,indent=2)+'\n')
        print(json.dumps({'reader_validation':'passed','binary_bytes':records['binary_bytes']}),flush=True)


if __name__=='__main__':main()
