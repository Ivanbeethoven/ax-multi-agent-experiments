import base64, datetime as dt, hashlib, json, mmap, os, re, struct, subprocess
from collections import defaultdict
B=4096
MAGIC=0xc03b3998
DEVICE='/dev/vdb'
TARGETS={557089,524320}
CST=dt.timezone(dt.timedelta(hours=8))
def epoch(s): return int(dt.datetime.fromisoformat('2026-11-11T'+s+'+08:00').timestamp())
start,end=epoch('18:03:40'),epoch('18:04:10')
def req(ok,msg):
 if not ok: raise ValueError(msg)
def be32(x,o=0): return struct.unpack_from('>I',x,o)[0]
def le16(x,o=0): return struct.unpack_from('<H',x,o)[0]
def le32(x,o=0): return struct.unpack_from('<I',x,o)[0]
def table_value(i):
 for j in range(8): i=(i>>1)^(0x82f63b78 if i&1 else 0)
 return i
table=[table_value(i) for i in range(256)]
def crc(seed,body):
 for b in body: seed=table[(seed^b)&255]^(seed>>8)
 return seed
req(crc(0xffffffff,b'123456789')^0xffffffff==0xe3069283,'CRC implementation')
def check(body,seed,offset):
 return crc(seed,body[:offset]+bytes(4)+body[offset+4:])==be32(body,offset)
def debug(cmd):
 return subprocess.check_output(['debugfs','-R',cmd,DEVICE],stderr=subprocess.DEVNULL,text=True)
req(os.stat('/srv/ax-metadata').st_dev==os.stat(DEVICE).st_rdev,'Wrong data disk')
req(debug('blocks /redis/appendonlydir').split()==['557089'],'Wrong directory block')
rows=debug('extents <8>').splitlines()
ranges=[tuple(map(int,m.groups())) for row in rows if (m:=re.search(r'\d+/\s*\d+\s+\d+/\s*\d+\s+(\d+)\s*-\s*(\d+)\s+(\d+)\s*-\s*(\d+)\s+(\d+)\s*$',row))]
req(len(ranges)==2 and [(a,b) for a,b,*_ in ranges]==[(0,32767),(32768,65535)],'Unexpected journal extent map')
fd=os.open(DEVICE,os.O_RDONLY|os.O_CLOEXEC|os.O_DIRECT)
try:
 pieces=[]
 for lo,hi,phy1,phy2,n in ranges:
  req(n==hi-lo+1==phy2-phy1+1,'Bad extent')
  for off in range(0,n,2048):
   length=min(2048,n-off)*B
   with mmap.mmap(-1,length) as mem:
    req(os.preadv(fd,[mem],(phy1+off)*B)==length,'Short direct journal read')
    pieces.append(mem[:])
 req(len(pieces)==32,'Incomplete ring')
 ring=memoryview(b''.join(pieces))
finally: os.close(fd)
def block(i): return ring[i*B:(i+1)*B]
sb=bytes(block(0))
req(be32(sb)==MAGIC and be32(sb,4)==4,'Bad JBD2 superblock')
req((be32(sb,12),be32(sb,16),be32(sb,20))==(B,65536,1),'Bad journal dimensions')
req(be32(sb,40)==0x13,'Need revoke/csum3/64bit only; no fast commit')
boot=open('/proc/sys/kernel/random/boot_id').read().strip()
req(boot=='9a6ebc7c-3b6a-4819-8720-d8e61ff5941e','Original metadata boot required')
serial=subprocess.check_output(['lsblk','-dn','-o','SERIAL',DEVICE],text=True).strip()
req(serial=='bp1gy3cfuj9jik0upt3k','Original disk serial required')
uuid=sb[48:64]
seed=crc(0xffffffff,uuid)
def wrap(i): return i if i<65536 else i-65535
commits={}
for i in range(1,65536):
 data=block(i)
 if be32(data)!=MAGIC or be32(data,4)!=2: continue
 sec,nsec=struct.unpack_from('>QI',data,48)
 if not start<=sec<=end or not 0<=nsec<10**9: continue
 raw=bytes(data)
 seq=be32(data,8)
 if check(raw,seed,16):
  req(seq not in commits,'Duplicate committed sequence')
  commits[seq]=dict(journal_block=i,utc=dt.datetime.fromtimestamp(sec,dt.timezone.utc).isoformat(),
                    epoch_seconds=sec,ns=nsec,sha256=hashlib.sha256(raw).hexdigest())
req(commits,'No checksum-valid committed records in the intended window')
def directory(data):
 offset=0; found={}
 while offset<B:
  inode,length,chars,kind=struct.unpack_from('<IHBB',data,offset)
  req(length>=8 and length%4==0 and offset+length<=B and chars<=length-8,'Malformed directory block')
  if inode and kind!=222:
   name=bytes(data[offset+8:offset+8+chars]).decode('ascii')
   req(name not in found,'Duplicate directory name')
   found[name]=inode
  offset+=length
 return found
def inodes(data):
 result={}
 for ino in range(524289,524297):
  row=data[(ino-524289)*256:(ino-524288)*256]
  flags=le32(row,32)
  units=B if flags&0x40000 else 512
  result[str(ino)]=dict(mode=le16(row),size=le32(row,4)+(le32(row,108)<<32),
       allocated=(le32(row,28)+(le16(row,116)<<32))*units,links=le16(row,26),flags=flags,
       deletion_time=le32(row,20),generation=le32(row,100),sectors_lo=le32(row,28),blocks_high=le16(row,116))
 return result

def chain(first,seq):
 current=first
 parts=[]
 for _ in range(32):
  raw=bytes(block(current))
  req(be32(raw)==MAGIC and be32(raw,8)==seq,('Noncontiguous transaction',seq,current))
  kind=be32(raw,4)
  parts.append(dict(journal_block=current,sha256=hashlib.sha256(raw).hexdigest(),raw_base64=base64.b64encode(raw).decode()))
  if kind==2:
   req(current==commits[seq]['journal_block'] and check(raw,seed,16),'Wrong commit checksum or position')
   return parts
  req(kind in (1,5) and check(raw,seed,B-4),'Invalid transaction metadata')
  if kind==5:
   count=be32(raw,12);req(16<=count<=B-4 and (count-16)%8==0,'Malformed revoke')
   revoked={struct.unpack_from('>Q',raw,p)[0] for p in range(16,count,8)}
   req(not TARGETS&revoked,'Target revoked in this transaction')
   current=wrap(current+1);continue
  p=12; consumed=0
  while p+16<=B-4:
   low,flags,high,want=struct.unpack_from('>4I',raw,p);p+=16
   req(flags&~15==0 and not flags&4,'Unknown/deleted descriptor flag')
   if not flags&2:p+=16
   j=wrap(current+1+consumed);payload=bytes(block(j));consumed+=1
   req(crc(crc(seed,struct.pack('>I',seq)),payload)==want,('Bad contiguous data checksum',seq,j))
   # Store only scoped metadata blocks; every payload was checksum-verified.
   if low+(high<<32) in TARGETS:
    parts.append(dict(journal_block=j,sha256=hashlib.sha256(payload).hexdigest(),raw_base64=base64.b64encode(payload).decode()))
   if flags&8:break
  req(flags&8,'Unterminated descriptor')
  current=wrap(current+1+consumed)
 raise ValueError('Too many transaction parts')

dirs=[]
inode_matches=[]
for i in range(1,65536):
 data=block(i)
 if be32(data)!=MAGIC or be32(data,4)!=1: continue
 seq=be32(data,8)
 if seq not in commits: continue
 desc=bytes(data)
 pos=12
 offset=1
 tags=[]
 while pos+16<=B-4:
  low,flags,high,want=struct.unpack_from('>4I',desc,pos); pos+=16
  if flags&~15: break
  if not flags&2:
   pos+=16
  fsblock=low+(high<<32)
  if fsblock in TARGETS:
   tags.append((fsblock,flags,want,wrap(i+offset)))
  offset+=1
  if flags&8: break
 if not tags: continue
 req(flags&8 and check(desc,seed,B-4),('Invalid descriptor',seq,i))
 for dest,flags,want,j in tags:
  raw=bytes(block(j))
  req(crc(crc(seed,struct.pack('>I',seq)),raw)==want,('Invalid block tag',seq,i,j,dest))
  req(not flags&4,('Deleted tag',seq,dest))
  if flags&1: raw=struct.pack('>I',MAGIC)+raw[4:]
  row=dict(sequence=seq,commit=commits[seq],descriptor_block=i,journal_block=j,destination_block=dest,
           tag_crc32c=f'{want:08x}',sha256=hashlib.sha256(raw).hexdigest())
  if dest==557089: dirs.append(dict(row,entries=directory(raw)))
  if dest==524320: inode_matches.append(dict(row,inodes=inodes(raw)))
dirs.sort(key=lambda x:x['sequence'])
inode_matches.sort(key=lambda x:x['sequence'])
selected=[]
previous=None
for r in inode_matches:
 # Only save records around directory changes and before/after extent deallocation;
 # continuous append-only records are counted but do not need to be printed.
 sig=tuple((r['inodes'][str(ino)]['generation'],r['inodes'][str(ino)]['links'],r['inodes'][str(ino)]['deletion_time']) for ino in range(524291,524297))
 close=any(abs(r['sequence']-d['sequence'])<=2 for d in dirs)
 if close or sig!=previous: selected.append(r)
 previous=sig

seqs=sorted(commits)
req(seqs==list(range(seqs[0],seqs[-1]+1)),'Commit history has a gap')
req([r['sequence'] for r in inode_matches]==seqs,'Inode block history has a gap')
chains={}
for r in dirs+selected:
 key=str(r['sequence'])
 if key not in chains:chains[key]=chain(r['descriptor_block'],r['sequence'])
body=dict(schema='c4-aof-journal-targeted-read-v2',captured_at=dt.datetime.now(dt.timezone.utc).isoformat(),
 scope='Direct read-only original data device; CRC-verified scoped metadata and contiguous commits. Not capacity admission.',
 device=DEVICE,boot_id=boot,disk_serial=serial,block_bytes=B,dir_block=557089,inode_table_block=524320,
 journal_extents=ranges,journal_superblock_base64=base64.b64encode(sb).decode(),window_cst=['18:03:40','18:04:10'],
 valid_commits=len(commits),first_sequence=seqs[0],last_sequence=seqs[-1],commit_sequence_contiguous=True,
 target_inode_blocks=len(inode_matches),all_target_tag_checksums_passed=True,
 directory_records=dirs,selected_inode_records=selected,selected_transaction_chains=chains)
print(json.dumps(body,indent=2))
