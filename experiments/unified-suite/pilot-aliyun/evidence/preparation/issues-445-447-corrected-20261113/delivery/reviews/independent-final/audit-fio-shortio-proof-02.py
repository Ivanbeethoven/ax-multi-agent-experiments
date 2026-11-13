from pathlib import Path
import hashlib,json,re,tarfile
P=Path('/home/dev/ax-perf-issues-validation/fio-shortio-proof-02.tar.gz')
O=P.parent/'independent-review-7b844'
H=lambda b:hashlib.sha256(b).hexdigest()
checks=[]
def ck(n,v):
    checks.append(n)
    assert v,n
ck('archive',H(P.read_bytes())=='16a4e4d93e1f2d47c55a30b650fa5b67d65b393caf75c57b39aa32d56a9c9dca')
with tarfile.open(P) as t:
    ck('members',len(t.getmembers())==42 and all(m.isfile() for m in t.getmembers()))
    data={m.name:t.extractfile(m).read() for m in t.getmembers()}
ck('manifest',H(data['manifest.json'])=='46a76d1b315f62999eaaaf01981e0f55aad757c482f9d689150a737da8af1212')
man=json.loads(data['manifest.json']);ck('payload count',len(man['files'])==41)
for m in man['files']:ck(m['path'],len(data[m['path']])==m['bytes'] and H(data[m['path']])==m['sha256'])
J=lambda p:json.loads(data[p])
repro=J('reproduction/receipt.json');abi=J('independent/offsets.json');proof=J('independent/receipt.json')
for n,v in proof['sources'].items():
    ck('independent source hash '+n,H(data['independent/official-source/'+n])==v['sha256'])
    if 'reproduction/source/'+n in data:ck('reproduction source equality '+n,data['reproduction/source/'+n]==data['independent/official-source/'+n])
ck('compile receipt',proof['compile_exit_code']==0 and proof['passed'] and not data['independent/compile.log'])
ck('probe source',H(data['independent/offsets.c'])==proof['probe_source_sha256'] and data['independent/offsets.c'].count(b'_Static_assert')==6)
ck('ABI receipt equality',abi==proof['abi'])
ck('ABI layout',abi['sizeof_uint64_t']==8 and abi['total_io_u_bytes']==32 and abi['short_io_u_offset']==abi['total_io_u_offset']+32 and abi['ddir_sync_file_range']==5 and abi['ddir_write']==1 and abi['ddir_sync_predicate']==1)
ck('out of bounds alias address',abi['total_io_u_offset']+5*8==abi['short_io_u_offset']+1*8==abi['range_sync_index_address_offset']==abi['write_short_address_offset']==150776)
ck('source completion increments raw index',b'td->ts.total_io_u[io_u->ddir]++;' in data['reproduction/source/ioengines.c'])
ck('adjacent arrays',re.search(rb'total_io_u\[DDIR_RWDIR_SYNC_CNT\];\s*uint64_t short_io_u\[DDIR_RWDIR_CNT\]',data['reproduction/source/stat.h']) is not None)
ck('JSON exposes short counter',b'"short_ios", ts->short_io_u[ddir]' in data['independent/official-source/stat.c'])
expected=H(b'\x5a'*(16<<20));results=[]
for case,expected_sync in [('no-sync',0),('sync',4095)]:
    cmd=J('reproduction/'+case+'-command.json');fio=J('reproduction/'+case+'.json');j=fio['jobs'][0]
    ck(case+'exit',cmd['exit_code']==0 and j['error']==0)
    ck(case+'version',fio['fio version']=='fio-3.39' and data['reproduction/version.log'].strip()==b'fio-3.39')
    rows=data['reproduction/'+case+'.strace'].decode().splitlines()
    target='/data-'+case+'/brewfs-bigwrite.0.0'
    writes=[l for l in rows if 'pwrite64(' in l and target in l]
    reads=[l for l in rows if 'pread64(' in l and target in l]
    syncs=[l for l in rows if 'sync_file_range(' in l and target in l]
    fsyncs=[l for l in rows if 'fsync(' in l and target in l]
    for direction,seq in [('write',writes),('read',reads)]:
        matches=[re.search(r', (\d+), (\d+)\)\s+= (-?\d+)$',l) for l in seq]
        ck(case+direction+'all4096',len(seq)==4096 and all(m and int(m[1])==int(m[3])==4096 for m in matches))
        ck(case+direction+'exact offsets',[int(m[2]) for m in matches]==list(range(0,16<<20,4096)))
        ck(case+direction+'JSON bytes',j[direction]['io_bytes']==16<<20 and j[direction]['total_ios']==4096 and j[direction]['drop_ios']==0)
    ck(case+'sync count and success',len(syncs)==expected_sync and all(l.endswith('= 0') for l in syncs))
    ck(case+'fsync',len(fsyncs)==1 and fsyncs[0].endswith('= 0'))
    ck(case+'short field',j['write']['short_ios']==expected_sync and j['read']['short_ios']==0)
    recorded=next(v for v in repro['results'] if v['case']==case)
    ck(case+'known hash recorded',recorded['data_sha256']==expected)
    ck(case+'pattern verification',j['job options']['verify']=='pattern' and j['job options']['verify_pattern']=='0x5a' and j['job options']['do_verify']==j['job options']['verify_fatal']=='1')
    results.append(dict(case=case,pwrite_count=len(writes),pread_count=len(reads),requested_and_returned_bytes_each=4096,exact_offsets_complete=True,sync_file_range_success_count=len(syncs),fsync_success_count=len(fsyncs),json_write_short_ios=j['write']['short_ios'],json_read_short_ios=j['read']['short_ios'],recorded_data_hash_matches_independent_known_hash=True))
def normalized(case):
    a=J('reproduction/'+case+'-command.json')['argv'];idx=next(i for i,v in enumerate(a) if v.endswith('/fio'))
    return [v for v in a[idx+1:] if not v.startswith(('--directory=','--output=','--sync_file_range='))]
ck('only intended FIO option change',normalized('no-sync')==normalized('sync'))
report=dict(schema='independent-fio-shortio-proof-review-v1',decision='UPSTREAM_FIO339_ACCOUNTING_CAUSE_CONFIRMED_LOCAL_REPRODUCTION',
    cloud_tools_same_binary_confirmed=False,integrated_pr_qualified=False,
    evidence=dict(archive=str(P),sha256=H(P.read_bytes()),manifest_sha256=H(data['manifest.json']),payload_members=41,total_members=42),checks_passed=len(checks),results=results,
    known16MiB_5a_sha256=expected,local_binary_sha256=repro['binary_sha256'],abi=abi,
    source_anchors={'ioengines.c':'433-438 (synchronous completion), 444-446 (queued completion)','io_ddir.h':'4-17 enum and 59-62 sync predicate','stat.h':'216-218 arrays','stat.c':'1498-1500 JSON output','backend.c':'478-479 actual short count guarded by ddir_rw'},
    conclusion='In the archived unmodified fio3.39 local build, all4096 actual pwrite and all4096 verification pread calls return the requested4096 bytes. Exactly4095 successful range-sync calls yield write.short_ios4095, while the otherwise equivalent no-range-sync run yields0. Real-header ABI and source identify the raw direction5 increment beyond total_io_u[4] at the address of short_io_u[WRITE1]. This establishes an accounting defect for this source/build, not file short writes.',
    limits='The payload data file and executable are not included in this small archive. The saved data hash equals an independently computed known pattern; syscall results and fio verification are directly rechecked. Upstream source/ABI and this local executable are not the cloud tools-image executable. The same-image regular-file diagnostic remains the final identity binding; no cloud performance run was touched by this review.',
    preservation='Original eight formal short_ios4095 values remain unchanged. Original unarchived run01 is explicitly not used for acceptance. Do not rewrite the count to zero, use diagnostic timings as performance, or claim pure write-only CPU for the write+verification+strict-drain target.')
out=O/'fio-shortio-proof-independent-review-02.json';out.write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'checks':len(checks),'decision':report['decision'],'review_sha256':H(out.read_bytes()),'results':results},indent=2))
