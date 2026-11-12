"""Verify a complete closed tree with bounded open directories and read workers."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import os
from pathlib import Path, PurePosixPath
import stat
import tempfile

VIRTUAL={'.stats','.config','.accesslog','.trash'}


def verify_tree(root, expected, workers=2):
    children={'':set()}
    for name,value in expected.items():
        path=PurePosixPath(name)
        if path.is_absolute() or not path.parts or str(path)!=name or any(p in ('..','.') for p in path.parts):
            raise ValueError('Unsafe manifest path')
        parent=str(path.parent) if path.parent!=PurePosixPath('.') else ''
        if parent and expected.get(parent,{}).get('type')!='directory':
            raise ValueError('Manifest parent is not a directory')
        children.setdefault(parent,set()).add(path.name)
        if value['type']=='directory': children.setdefault(name,set())
    rootfd=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    def open_directory(name):
        fd=os.dup(rootfd)
        try:
            for component in PurePosixPath(name).parts:
                child=os.open(component,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
                os.close(fd);fd=child
            return fd
        except BaseException:
            os.close(fd);raise
    def entry(parent,name,value):
        if value['type']=='link':
            info=os.stat(name,dir_fd=parent,follow_symlinks=False)
            if not stat.S_ISLNK(info.st_mode): raise ValueError('Expected symlink: '+name)
            actual={'type':'link','target':os.readlink(name,dir_fd=parent)}
        elif value['type']=='file':
            fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=parent)
            with os.fdopen(fd,'rb') as stream:
                info=os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode): raise ValueError('Expected regular file: '+name)
                actual={'type':'file','bytes':info.st_size,'sha256':hashlib.file_digest(stream,'sha256').hexdigest()}
        else: raise ValueError('Unexpected manifest type: '+name)
        actual['mode']=info.st_mode&0o7777
        if actual!=value: raise ValueError('Tree entry differs: '+name)
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            # FUSE directory handles can retain decoded pages. Close each
            # completed directory instead of retaining handles for the tree.
            for name,wanted in children.items():
                parent=open_directory(name)
                try:
                    if name and {'type':'directory','mode':os.fstat(parent).st_mode&0o7777}!=expected[name]:
                        raise ValueError('Directory mode differs: '+name)
                    with os.scandir(parent) as entries: actual={e.name for e in entries}
                    if not name: actual-=VIRTUAL
                    if actual!=wanted: raise ValueError('Directory members differ: '+name)
                    futures=[]
                    for leaf in wanted:
                        value=expected[name+'/'+leaf if name else leaf]
                        if value['type']!='directory': futures.append(pool.submit(entry,parent,leaf,value))
                    try:
                        for future in as_completed(futures): future.result()
                    finally:
                        for future in futures: future.cancel()
                        # Finish active readers before closing their parent FD.
                        for future in futures:
                            if not future.cancelled():
                                try: future.result()
                                except Exception: pass
                finally:
                    os.close(parent)
    finally:
        os.close(rootfd)
    return {'passed':True,'entries':len(expected),'directories':len(children),
            'workers':workers,'cached_parent_descriptors':True,'directory_handles':'one directory at a time'}


def write_probe(root):
    payload=bytes(range(256))*16
    fd,name=tempfile.mkstemp(prefix='.ax-handoff-probe-',dir=root)
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(payload);stream.flush();os.fsync(stream.fileno())
        if Path(name).read_bytes()!=payload: raise ValueError('Target write/read differs')
    finally:
        os.unlink(name)
    parent=os.open(root,os.O_RDONLY|os.O_DIRECTORY)
    try: os.fsync(parent)
    finally: os.close(parent)
    return {'passed':True,'bytes':len(payload),'fsync':True,'probe_removed':True}


if __name__=='__main__':
    import json
    import sys
    print(json.dumps(verify_tree(Path(sys.argv[1]),json.load(sys.stdin))))
