#!/usr/bin/env python3
"""Check a frozen C4 runtime's entry points and imports on the coordinator."""
from pathlib import Path
import hashlib,json,os,subprocess,sys
import argparse
p=argparse.ArgumentParser(description='Audit the local C4 runner without contacting any worker')
p.add_argument('--runtime-repo',type=Path,required=True)
p.add_argument('--stage',type=Path,required=True)
p.add_argument('--out',type=Path,required=True)
a=p.parse_args(); repo=a.runtime_repo.resolve(strict=True); stage=a.stage.resolve(strict=True)
assert a.out.is_absolute() and a.out.is_relative_to(stage) and not a.out.exists()
prefix='experiments/unified-suite/pilot-aliyun/scripts/'
cli=['run-c4-batch.py','run-c4-fork-baseline.py','capacity-check.py','prepare-c4-controller.py','calibrate-c4.py','check-c4-calibration.py','admit-c4.py','run-c4.py','c4-fork-node.py','pilot-node.py','make-observer.py','deploy-c4-code.py','setup-c4-control.py','install-code.py','continue-flat-c3-after-base.py','fork-oracle.py','verify-latest.py','run-c1.py','metric-collector.py']
modules=['bench.runtime.c4_verify','bench.runtime.pilot_context','bench.runtime.fork_work','bench.runtime.common','bench.runtime.cohort','bench.runtime.fleet','bench.runtime.node','bench.runtime.observe','bench.runtime.prepare','bench.runtime.workloads','bench.runtime.pilot_pools','bench.suite_state','bench.suite_validate','bench.suite_report','bench.suite_plan','bench.suite_support','bench.suite_mount','bench.archive_check','bench.experiment_step','bench.fs_step','bench.ssh_inventory']
entry_paths=[repo/(prefix+n) for n in cli]+[stage/'continue-c4-native-flat.py',repo/'bench/fs_step.py']
env={'PATH':'/usr/bin:/bin','HOME':'/nonexistent','PYTHONDONTWRITEBYTECODE':'1','PYTHONNOUSERSITE':'1'}
help_receipts={}
for path in entry_paths:
    args=[sys.executable,str(path),'--help']; z=subprocess.run(args,cwd=repo,env=env,capture_output=True,text=True,timeout=4)
    help_receipts[path.name]={'exit':z.returncode,'usage_printed':'usage:' in z.stdout,'stderr':z.stderr.strip(),'sha256':hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None}
script=r'''
import ast,importlib,importlib.util,json,pathlib,re,runpy,sys
repo=pathlib.Path(sys.argv[1]); targets=json.loads(sys.argv[2]); entries=[pathlib.Path(p) for p in json.loads(sys.argv[3])]
sys.path[:0]=[str(repo),str(repo/'bench'),str(repo/'scripts'),str(entries[0].parent)]
def prevent(event,args):
    if event in ('socket.connect','socket.getaddrinfo','socket.bind','subprocess.Popen','os.system','os.exec','os.spawn'):
        raise RuntimeError('Network/process actions forbidden in import audit: '+event)
sys.addaudithook(prevent)
for target in targets:
    module=importlib.import_module(target)
    if not pathlib.Path(module.__file__).resolve().is_relative_to(repo):raise ValueError('Wrong runtime import: '+target)
for path in entries: runpy.run_path(str(path),run_name='c4_import_audit')
initial=[p for p in entries if p.is_relative_to(repo)]
for module in list(sys.modules.values()):
    name=getattr(module,'__file__',None)
    if name and (p:=pathlib.Path(name).resolve()).is_relative_to(repo) and p.suffix=='.py': initial.append(p)
seen=set(); imports=set(); resolved={}; missing=[]; python_files=[]
while initial:
    path=initial.pop()
    if path in seen:continue
    seen.add(path); tree=ast.parse(path.read_text()); package='.'.join(path.relative_to(repo).parts[:-1])
    def add_import(name):
        if name: imports.add(name)
    for item in ast.walk(tree):
        if isinstance(item,ast.Import):
            for a in item.names:add_import(a.name)
        elif isinstance(item,ast.ImportFrom):
            name=importlib.util.resolve_name('.'*item.level+(item.module or ''),package) if item.level else item.module
            add_import(name)
            for alias in item.names:
                if name and ((repo/(name.replace('.','/')+'/'+alias.name+'.py')).is_file() or (repo/(name.replace('.','/')+'/'+alias.name+'/__init__.py')).is_file()):add_import(name+'.'+alias.name)
        elif isinstance(item,ast.Constant) and isinstance(item.value,str):
            for name in re.findall(r'(?m)^\s*from (bench(?:\.\w+)+) import ',item.value):add_import(name)
            if re.fullmatch(r'[A-Za-z_/-][A-Za-z0-9_./-]*\.py',item.value):
                x=item.value.lstrip('/'); choices=[repo/x,path.parent/x,repo/'bench'/x,repo/'scripts'/x,entries[0].parent/x]
                found=next((q.resolve() for q in choices if q.is_file()),None)
                if found and found.is_relative_to(repo):
                    initial.append(found); python_files.append(str(found.relative_to(repo)))
    for name in sorted(imports-set(resolved)):
        try: spec=importlib.util.find_spec(name)
        except (ModuleNotFoundError,ValueError): spec=None
        resolved[name]=spec.origin if spec else None
        if not spec: missing.append(name)
        elif spec.origin and (q:=pathlib.Path(spec.origin).resolve()).is_relative_to(repo) and q.suffix=='.py':initial.append(q)
from bench.runtime import c4_verify
batch=runpy.run_path(str(entries[0]),run_name='c4_batch_import_audit')
assert batch['trial_verification'](c4_verify.TOUCHED,0,None)=={'verification_policy':c4_verify.REFERENCE}
assert batch['trial_verification'](c4_verify.TOUCHED,1,{'path':'x','sha256':'y'})['verification_policy']==c4_verify.TOUCHED
print(json.dumps({'top_level_imports_passed':True,'nested_python_dependencies':len(resolved),'resolved_local_files':sorted(str(p.relative_to(repo)) for p in seen),'other_python_entry_references':sorted(set(python_files)),'missing':sorted(set(missing)),'guarded_network_and_process_actions':True,'touched_dispatch_smoke':True}))
'''
z=subprocess.run([sys.executable,'-c',script,str(repo),json.dumps(modules),json.dumps([str(p) for p in entry_paths])],cwd=repo,env=env,capture_output=True,text=True,timeout=12)
proof={'passed':z.returncode==0 and all(x['exit']==0 and x['usage_printed'] for x in help_receipts.values()),'scope':'Coordinator-only CLI help, real and recursive import audit for exact C4 payload; no worker calls or state writes','runtime':str(repo),'cli':help_receipts,'requested_real_imports':modules,'dynamic_status':z.returncode,'dynamic_stderr':z.stderr}
if z.returncode==0:
 proof['import_closure']=json.loads(z.stdout); proof['passed']=proof['passed'] and not proof['import_closure']['missing']
with a.out.open('x') as f:json.dump(proof,f,indent=2);f.write('\n')
print(json.dumps({'passed':proof['passed'],'CLI':len(help_receipts),'failed_CLI':{k:v for k,v in help_receipts.items() if v['exit'] or not v['usage_printed']},'dynamic_status':z.returncode,'stderr':z.stderr,'nested_dependencies':proof.get('import_closure',{}).get('nested_python_dependencies'),'missing':proof.get('import_closure',{}).get('missing'),'receipt_sha256':hashlib.sha256(a.out.read_bytes()).hexdigest()},indent=2))
sys.exit(0 if proof['passed'] else 1)
