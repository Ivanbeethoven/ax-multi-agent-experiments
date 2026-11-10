"""Bounded import fsync, shared by base preparation and private full copies."""
from concurrent.futures import ThreadPoolExecutor
from itertools import islice
import os


def fsync_import(root):
    directories=[root]
    def files():
        for p in root.rglob('*'):
            if p.is_symlink():raise ValueError('Synthetic C3 source has no symlinks')
            if p.is_dir():directories.append(p)
            elif p.is_file():yield p
            else:raise ValueError('Unexpected imported inode')
    def one(p):
        with p.open('rb') as f:os.fsync(f.fileno())
    iterator=files();count=0
    with ThreadPoolExecutor(max_workers=8) as pool:
        while batch:=list(islice(iterator,1024)):
            list(pool.map(one,batch));count+=len(batch)
    for p in sorted(directories,key=lambda x:len(x.parts),reverse=True):
        fd=os.open(p,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(fd)
        finally:os.close(fd)
    return count
