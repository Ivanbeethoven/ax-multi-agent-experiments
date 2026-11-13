from pathlib import Path
import copy,hashlib,json,statistics,yaml
R=Path('/home/dev/ax-perf-issues-validation/corrected-runtime-review-11')
C=R/'home/dev/ax-perf-issues-corrected-validation/compose'
O=Path('/home/dev/ax-perf-issues-validation/independent-review-7b844')
PINS={'baseline':('4f982f45c153a0c31e3e9cd8ff642ec7659c98fd','0b0a2743aa968f497782d823cb068cd421eb840f82d5739a363d3bc3623c8679'),'candidate':('c8f5f16aefe811540782b952481982179a1bfd6f','905c4e4ca35162ae6a1ab2de03f4fa18194ef64d9afcbe20cff9bbefd570ce90')}
TOOL='fio-randwrite'
DEBT=['brewfs_writeback_recent_pending_upload_bytes','brewfs_writeback_dirty_bytes','brewfs_buffer_dirty_bytes','brewfs_writeback_live_dirty_bytes','brewfs_writeback_live_slices','brewfs_writeback_recent_pending_upload_slices','brewfs_writeback_stage_inflight_bytes','brewfs_writeback_remote_upload_inflight_bytes','brewfs_writeback_metadata_inflight']
checks=[];inputs={}
def ck(n,b):checks.append({'name':n,'passed':bool(b)});assert b,n
def read(p):
 b=p.read_bytes();inputs[str(p)]=hashlib.sha256(b).hexdigest();return b.decode()
def j(p):return json.loads(read(p))
def at(p):return R/p.lstrip('/')
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def metrics(p):
 x={}
 for l in read(p).splitlines():
  v=l.split()
  if len(v)==2 and v[0].startswith('brewfs_'):
   try:x[v[0]]=int(v[1])
   except ValueError:pass
 return x
m=j(R/'manifest.json')
for x in m['files']:
 p=R/x['path'];ck('archive:'+x['path'],p.stat().st_size==x['bytes'] and digest(p)==x['sha256'])
matrix=j(C/'matrices/fixed-work-randwrite1-610/receipt.json')
ck('matrix scope',matrix['passed'] and matrix['historical_601_603_replaced'] is False and matrix['performance_accepted'] is False)
same_config=None;same_options=None;same_images=None;volumes=set();pairs=[]
for pair in matrix['pairs']:
 trial=pair['trial'];ck(f'{trial}: order',pair['order']==(['candidate','baseline'] if trial==611 else ['baseline','candidate']))
 arms={}
 for arm in pair['order']:
  label=f'ax447-corrected-{arm}-fixed-work-randwrite1-{trial}';root=C/'trials'/label;t=j(root/'receipt.json');a=at(t['artifact_directories'][0]);stored=pair['arms'][arm]
  ck(label+': gates',t['qualified'] and t['status']==0 and stored['correctness_passed'])
  ck(label+': exact identity',t['source']['commit']==t['binary']['commit']==PINS[arm][0] and t['binary']['sha256']==PINS[arm][1] and not t['source']['dirty'])
  if same_images is None:same_images=t['images']
  ck(label+': same actual image pins',t['images']==same_images)
  for key,path in [('case_harness_sha256',C/'fixed-work-610/run-fixed-case.py'),('container_harness_sha256',C/'fixed-work-610/run-fixed-container.sh'),('compose_sha256',C/'fixed-work-610/compose.json'),('harness_sha256',C/'fixed-work-610/run-fixed-host.sh')]:
   ck(label+': helper '+key,t[key]==digest(path))
  render=j(root/'rendered-compose.json');ck(label+': isolated project',render['name']==label)
  for name,v in render['volumes'].items():
   ck(label+': fresh owned volume '+name,v['name'].startswith(label+'_') and v['name'] not in volumes);volumes.add(v['name'])
  for service,cpus,mem in [('perf',3,16<<30),('redis',1,2<<30),('rustfs',2,8<<30)]:
   z=render['services'][service];ck(label+': resource '+service,float(z['cpus'])==cpus and int(z['mem_limit'])==mem)
  ck(label+': no profiling or diagnostic log',t['environment']['RUST_LOG']=='warn' and t['environment']['BREWFS_FUSE_OP_LOG']=='0' and t['environment']['BREWFS_FUSE_WRITEBACK']=='0')
  conf=yaml.safe_load(read(a/'backend.yml'));z=copy.deepcopy(conf);z['cache'].pop('root')
  if same_config is None:same_config=z
  ck(label+': same config except fresh cache root',same_config==z)
  ck(label+': explicit memory/SSD budgets',all(conf['cache'][k]==v for k,v in [('read_memory_bytes',4<<30),('write_memory_bytes',4<<30),('read_ssd_bytes',8<<30),('write_ssd_bytes',4<<30),('memory_budget_bytes',12<<30)]))
  ck(label+': default background not disabled',not conf.get('meta',{}).get('no_background_jobs',False) and not any('BACKGROUND' in k or 'COMPACT' in k for k in t['environment']))
  fio=j(a/'results/fio-randwrite.json');ck(label+': one reporting group',len(fio['jobs'])==1)
  job=fio['jobs'][0];opt=job['job options'];normal={k:v for k,v in opt.items() if k!='write_lat_log'}
  if same_options is None:same_options=normal
  ck(label+': same workload options',normal==same_options)
  ck(label+': fixed workload exactly',all(opt[k]==v for k,v in {'rw':'randwrite','bs':'4m','size':'512m','io_size':'1g','numjobs':'4','ioengine':'io_uring','iodepth':'1','direct':'1','randseed':'20261031','randrepeat':'1'}.items()) and 'runtime' not in opt and 'time_based' not in opt)
  ck(label+': full fixed bytes no short/error',job['error']==0 and job['write']['io_bytes']==4<<30 and job['write']['total_ios']==1024 and job['write']['short_ios']==job['write']['drop_ios']==0 and job['read']['io_bytes']==0)
  argv=read(a/'tools/fio-randwrite.argv');ck(label+': exact single job declaration',argv.count('--name=')==1 and '--time_based' not in argv and '--runtime' not in argv and '--io_size=1g' in argv)
  cpu=j(a/'client-cpu-summary.json')['tools'][0];hz=int(read(a/'client-cpu-clock-ticks.txt'));observations=[]
  for line in read(a/'client-cpu-raw.tsv').splitlines():
   tool,phase,when,raw=line.split('\t',3);ck(label+': only expected CPU tool',tool==TOOL);f=raw[raw.rfind(')')+2:].split()
   observations.append({'observed_ns':int(when),'pid':int(raw.split()[0]),'starttime_ticks':int(f[19]),'user_ticks':int(f[11]),'system_ticks':int(f[12])})
  ck(label+': raw CPU observations',observations==cpu['observations'] and len(observations)==3 and len({(v['pid'],v['starttime_ticks'])for v in observations})==1)
  ticks=[v['user_ticks']+v['system_ticks'] for v in observations];active=(ticks[1]-ticks[0])/hz;drain=(ticks[2]-ticks[1])/hz;full=(ticks[2]-ticks[0])/hz
  ck(label+': CPU values',abs(active-cpu['tool_cpu']['total_s'])<1e-9 and abs(drain-cpu['post_tool_drain_cpu']['total_s'])<1e-9 and abs(full-cpu['tool_and_drain_cpu']['total_s'])<1e-9)
  phase={}
  for line in read(a/'phase-boundaries.tsv').splitlines():
   tool,name,ns=line.split('\t');ck(label+': phase tool',tool==TOOL);phase[name]=int(ns)
  act=(phase['tool_return']-phase['tool_start'])/1e9;wall=(phase['post-write_extended_drained']-phase['tool_start'])/1e9
  ck(label+': ordered wall boundaries',phase['tool_start']<phase['tool_return']<phase['post-write_drain_start']<=phase['post-write_extended_drained'])
  ck(label+': independent full formulas',abs(stored['active_s']-act)<1e-9 and abs(stored['active_plus_strict_drain_s']-wall)<1e-9 and abs(stored['cpu_per_gib_s']-full/4)<1e-9)
  snapshots=[metrics(p)for p in sorted((a/'stats-timeseries/fio-randwrite-post-write').glob('*.txt'))]
  ck(label+': full strict drain debt',len(snapshots)>=3 and all(all(x[k]==0 for k in DEBT)for x in snapshots[-3:]))
  ck(label+': strict drain PUT stable',len({(x['brewfs_s3_put_ops_total'],x['brewfs_s3_put_bytes_total'])for x in snapshots[-3:]})==1)
  before=metrics(a/'diagnostics/stats-fio-randwrite-before.txt');ck(label+': only original28B sanity preflight counters',before['brewfs_fuse_write_ops_total']==1 and before['brewfs_fuse_write_bytes_total']==28 and before['brewfs_s3_put_ops_total'] in [0,1] and before['brewfs_s3_put_bytes_total']==32*before['brewfs_s3_put_ops_total'])
  durpath=a/'remote-durability/fio-randwrite';dur=j(durpath/'receipt.json');expected=j(durpath/'expected.json');observed=j(durpath/'observed.json')
  ck(label+': four complete512MiB warm/cold hashes',expected==observed and len(expected)==4 and all(v['bytes']==512<<20 for v in expected) and len({v['path'] for v in expected})==4)
  ck(label+': cold independent cache process',dur['passed'] and dur['new_cache_was_empty'] and dur['old_cache_preserved'] and dur['old_mount']['pid']!=dur['new_mount']['pid'] and dur['unmount']['old_process_terminated'] and not dur['unmount']['forced'])
  ck(label+': cold identity same exact binary',dur['old_mount']['executable_sha256']==dur['new_mount']['executable_sha256']==PINS[arm][1] and dur['old_mount']['argv']==dur['new_mount']['argv'])
  ck(label+': validation outside measurement',dur['started_unix_ns']>observations[-1]['observed_ns'] and dur['timing_outside_measured_tool_and_strict_drain'])
  ck(label+': no upload/recovery failures',dur['log_failures_before']==dur['log_failures_after']==[])
  gate=j(root/'services/error-gate.json');ck(label+': service error gate',gate['passed'] and gate['error_count']==0 and gate['errors']==[] and digest(root/'services/rustfs.log')==gate['log_sha256'])
  ck(label+': cleanup',read(root/'remaining-owned-containers.json').strip()=='')
  capacities=[json.loads(x)for x in read(a/'capacity-scenes.jsonl').splitlines()];ck(label+': capacity all scene boundaries',len(capacities)==2 and all(v['passed'] for v in capacities))
  arms[arm]={'write_bytes':4<<30,'io_count':1024,'active_wall_s':act,'full_wall_s':wall,'post_return_wall_s':wall-act,'active_cpu_s':active,'drain_cpu_s':drain,'full_cpu_s':full,'cpu_s_per_gib':full/4,'write_p99_ns':job['write']['lat_ns']['percentile']['99.000000'],'artifact':str(a),'before_preflight_fuse_write_bytes':28,'before_put_ops':before['brewfs_s3_put_ops_total'],'before_put_bytes':before['brewfs_s3_put_bytes_total'],'cold_hashes':expected}
 b=arms['baseline'];n=arms['candidate'];pairs.append({'trial':trial,'order':pair['order'],'arms':arms,'candidate_over_baseline':{'active_wall':n['active_wall_s']/b['active_wall_s'],'full_wall':n['full_wall_s']/b['full_wall_s'],'full_cpu_per_gib':n['cpu_s_per_gib']/b['cpu_s_per_gib']}})
stop=j(next(R.rglob('final-runtime-stopped-04/receipt.json')))
ck('final runtime stopped',stop['runtime_stopped'] and stop['heavy_lock_acquired'])
report={'schema':'independent-fixedwork11-review-v1','checks_passed':len(checks),'archive_payload_files':len(m['files']),'archive_sha256':'bef783c9277a6d4a8c232e0b999e5e14882ac4ad456eecd8a54216f14be124ef','pairs':pairs,
 'decision':'FIXED_WORK_FOREGROUND_PLUS_STRICT_DRAIN_GUARD_PASSES_LIMITED_SCOPE','scope':['Fresh project-specific Redis/S3/cache volumes, equal4GiB write work and1024IO,4x512MiB working files, same seed/options and configured budgets; FIO scramble payload is not claimed byte-identical across arms.','All6 cold remount full SHA and client/server/drain/capacity checks pass. No old large predecessor workload exists. The unchanged sanity check writes/reads/deletes28B before timing: five arms already show1PUT32B, candidate610 shows0PUT before timing. This bounded preflight tail is retained, not subtracted. Default background remains configured.','Complete tool+strictdrain CPU/GiB and wall are guards, not an improvement claim. Active latency redistribution is preserved, including610 about2.96x; no universal foreground-latency claim.','This does not include later cold-remount maintenance CPU, all600s cycles or full mount lifecycle. Old601/603 numbers remain valid historical-state sequence observations with proven predecessor-mixing limits.'],
 'same_effective_config':same_config,'same_workload_options':same_options,'input_sha256':inputs}
f=O/'corrected-fixedwork11-review-01.json';assert not f.exists();f.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n');(O/'corrected-fixedwork11-checks-01.json').write_text(json.dumps(checks,indent=2)+'\n')
print(json.dumps({'path':str(f),'sha256':digest(f),'checks':len(checks),'pairs':[{k:v for k,v in p.items() if k!='arms'} for p in pairs],'armvalues':[{a:{k:v for k,v in arm.items() if k not in ['cold_hashes','artifact']} for a,arm in p['arms'].items()} for p in pairs]},indent=2))
