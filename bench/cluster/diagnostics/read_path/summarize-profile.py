import json,re,sys
from pathlib import Path
root=Path(sys.argv[1])
rows=[]
for path in sorted(root.rglob('cold-perf.txt')):
 symbols=[]
 for line in path.read_text().splitlines():
  found=re.match(r'\s*(\d+\.\d+)%\s+\[([k.])\]\s+(\S+)',line)
  if found:symbols.append(dict(percent=float(found[1]),kind=found[2],symbol=found[3]))
 d=path.parent
 memory={}
 for name in ('memory.peak','memory.current'):
  file=d/f'after-{name}'
  if file.exists():memory[name]=int(file.read_text())
 for phase in ('before','after'):
  file=d/f'{phase}-memory.stat'
  if file.exists():
   stat=dict(line.split() for line in file.read_text().splitlines())
   memory[phase]={k:int(stat[k]) for k in ('anon','file','kernel') if k in stat}
 rows.append(dict(raw_directory=str(d),memory=memory,symbols=symbols))
(root/'cpu-profile-summary.json').write_text(json.dumps(rows,indent=2))
print('Saved',len(rows),'profiles')
