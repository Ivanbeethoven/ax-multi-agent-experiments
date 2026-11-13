from pathlib import Path
import hashlib,json,re,shlex
R=Path('/home/dev/ax-perf-issues-validation')
P=R/'corrected-runtime-review-08';D=P/'diagnostic/fio-counter-actual-tools-01';O=R/'independent-review-7b844'
J=lambda p:json.loads(p.read_text());H=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
checks=[]
def ck(n,v):
    checks.append(n);assert v,n
manifest=J(P/'manifest.json')
for e in manifest['files']:
    if e['path'].startswith('diagnostic/'):
        q=P/e['path'];ck(e['path'],q.stat().st_size==e['bytes'] and H(q)==e['sha256'])
host=J(D/'host-receipt.json');r=J(D/'receipt.json');trace=J(D/'trace-check.json')
image='sha256:c8d98b130b7382e8fce8557e70242536db5b6f7c8654c86962c32d038f535645'
ck('actual fixed image and exit',host['image_id']==image and image in host['create_argv'] and host['exit_code']==0 and host['container_state']['ExitCode']==0 and not host['container_state']['Running'] and not host['container_state']['OOMKilled'])
ck('limits and network',host['actual_limits']==dict(NanoCpus=1000000000,Memory=536870912,NetworkMode='none'))
ck('ordinary filesystem', '/dev/vda3 ext4' in host['filesystem'] and '--device' not in host['create_argv'])
formal=R/'corrected-runtime-review-07'
for td in (formal/'trials').iterdir():ck('same formal tools image '+td.name,J(td/'receipt.json')['images']['tools']['id']==image)
source=formal/'artifacts/baseline/perf-run-1794525913-24164/tools/fio-bigwrite.argv'
ck('exact original formal argv',source.read_bytes()==(D/'original-fio-bigwrite.argv').read_bytes() and H(source)==r['source_argv_sha256'])
ck('version and binary identity recorded',r['version']=='fio-3.39' and r['fio_sha256']=='383b58e9f8fc2a0bfc9270fae73ac9491eefd0b375da9b79c57a7abeeff566ef' and r['passed'])
original=shlex.split(source.read_text())[2:];expected_hash=hashlib.sha256(b'\x5a'*(16<<20)).hexdigest();cases=[]
for label,n in [('off',0),('on',4095)]:
    saved=next(x for x in r['cases'] if x['label']==label)
    intended=[]
    for token in original:
        if token.startswith('--directory='):token='--directory=/proof/'+label+'-data'
        elif token.startswith('--output='):token='--output=/proof/'+label+'.json'
        elif token.startswith('--write_lat_log='):token='--write_lat_log=/proof/'+label+'_lat'
        elif token.startswith('--sync_file_range=') and label=='off':continue
        intended.append(token)
    ck(label+'exact option transformation',saved['argv']==['/usr/bin/fio',*intended])
    argv=J(D/(label+'.argv.json'));ck(label+'actual traced command',argv[argv.index('/usr/bin/fio'):]==saved['argv'] and 'trace=pwrite64,sync_file_range' in argv)
    raw=J(D/(label+'.json'));j=raw['jobs'][0]
    ck(label+'raw result',raw['fio version']=='fio-3.39' and len(raw['jobs'])==1 and j['error']==0 and saved['exit_code']==0)
    for direction in ['read','write']:ck(label+direction+'logical verified workload',j[direction]['io_bytes']==16<<20 and j[direction]['total_ios']==4096 and j[direction]['drop_ios']==0)
    ck(label+'raw short counts retained',j['write']['short_ios']==n and j['read']['short_ios']==0)
    ck(label+'known file receipt',saved['data_bytes']==16<<20 and saved['data_sha256']==expected_hash)
    lines=(D/(label+'.strace')).read_text().splitlines();writes=[l for l in lines if 'pwrite64(' in l];syncs=[l for l in lines if 'sync_file_range(' in l]
    parsed=[re.search(r', (\d+), (\d+)\) = (-?\d+)$',l) for l in writes]
    ck(label+'all pwrite full',len(parsed)==4096 and all(m and int(m[1])==int(m[3])==4096 for m in parsed))
    ck(label+'offset coverage',[int(m[2]) for m in parsed]==list(range(0,16<<20,4096)))
    ck(label+'sync syscall successes',len(syncs)==n and all(l.endswith('= 0') for l in syncs))
    ck(label+'trace receipt hash',next(x for x in trace['cases'] if x['label']==label)['trace_sha256']==H(D/(label+'.strace')))
    cases.append(dict(label=label,pwrite_full4096_count=len(writes),successful_sync_count=len(syncs),raw_short_ios=j['write']['short_ios'],known16MiB_sha256=expected_hash))
cpu=O/'corrected-cached-review-07.json';ck('prior CPU review preserved',H(cpu)=='15512f44680829670d993738e60ec6a2edf77292d42ac728c4670cb931e5326d')
local=O/'fio-shortio-proof-independent-review-02.json';ck('local source ABI reproduction preserved',H(local)=='6e12b44dac4a5c562290c2b5287dd18805f05b86289ad2aaa1e7997558dd5929')
report=dict(schema='independent-actual-tools-shortio-resolution-v1',decision='SHORT_IOS_ACCOUNTING_EXPLANATION_CLOSED_CACHED_CPU_TARGET_ACCEPTED',integrated_pr_qualified=False,checks_passed=len(checks),
    evidence=dict(directory=str(D),runtime08_tar_sha256='ddbb131ca0003384d2356e84f68b97952b5c3325a85a35a69464ed5735c2079f',host_receipt_sha256=H(D/'host-receipt.json'),probe_receipt_sha256=H(D/'receipt.json'),image_id=image,fio_binary_sha256=r['fio_sha256']),
    cases=cases,local_source_abi_review_sha256=H(local),prior_cached_review_sha256=H(cpu),
    conclusion='The frozen tools image used by all eight cached arms reproduces raw write.short_ios4095 only when4095 successful range-sync syscalls run between4096 complete4KiB pwrite calls. Original formal argv differs only in paths and the off control removing sync. Combined with the independently checked upstream3.39 source/ABI reproduction, this closes the accounting explanation without treating the raw field as actual short file writes.',
    read_scope='Actual-tools diagnostic did not trace pread. Raw fio JSON reports complete16MiB/4096 verification reads and the probe records the independently known full-file0x5a SHA. The separate local upstream proof traces every complete pread. Do not merge these scopes or claim cloud pread tracing.',
    cpu_conclusion='The prior independent698-check review establishes three corrected AB/BA/AB paired client CPU reductions of15.094,24.074,18.868percent (8,13,10ticks at100HZ). This is write plus in-tool pattern readback plus strict-drain BrewFS process CPU, excluding FIO/RustFS CPU. No wall throughput improvement claim.',
    preservation='All eight formal short_ios values, the earlier pending review, and diagnostics remain unchanged. No diagnostic timing is included in performance.',
    remaining='Overall integrated PR remains HOLD for repeated randwrite/direct1 CPU/GiB regression in full matrices601/603 and pending corrected workspace/long-row gates. Target improvement does not cancel another path regression.')
out=O/'fio-actual-tools-resolution-review-08.json';out.write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'checks':len(checks),'decision':report['decision'],'sha256':H(out),'cases':cases},indent=2))
