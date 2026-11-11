import json,os,pathlib
p=pathlib.Path('/srv/ax-objects-0');v=os.statvfs(p)
tags=('c1-7084af367e0bcfad-r1-native', 'cal-n-rw-on1', 'cal-n-rw-on2', 'cal-n-rw-on3')
print(json.dumps(dict(total_bytes=v.f_blocks*v.f_frsize,free_bytes=v.f_bavail*v.f_frsize,
                     free_inodes=v.f_favail,device=p.stat().st_dev,
                     roots={tag:dict(device=(q:=p/'o'/tag).lstat().st_dev,inode=q.lstat().st_ino,
                                     mtime_ns=q.lstat().st_mtime_ns) for tag in tags})))
