"""Serial passthrough FUSE recorder. All callbacks and write payloads persisted.

Records the mount boundary (including kernel mmap traffic), not application syscalls.
Local kernel locks/cache hits do not issue callbacks and are outside this format.
"""
import argparse
import errno
import hashlib
import json
import os
from pathlib import Path
import stat
import time
from fuse import FUSE, Operations, FuseOSError, fuse_get_context


class Recorder(Operations):
    def __init__(self, root, output):
        self.root = root.resolve()
        self.output = output
        output.mkdir(parents=True, exist_ok=False)
        (output / "blobs").mkdir()
        self.log = (output / "events.jsonl").open("x", buffering=1)
        self.seq = 0
        self.handles = {}
        self.next_handle = 0

    def path(self, p):
        if not p.startswith("/") or ".." in p.split("/"): raise FuseOSError(errno.EPERM)
        return self.root / p.lstrip("/")

    def alloc(self, fd):
        self.next_handle += 1
        self.handles[self.next_handle] = fd
        return self.next_handle

    def blob(self, data):
        digest = hashlib.sha256(data).hexdigest()
        path = self.output / "blobs" / digest
        if not path.exists(): path.write_bytes(data)
        return {"blob": digest, "len": len(data)}

    def __call__(self, op, *args):
        started = time.monotonic_ns()
        uid, gid, pid = fuse_get_context()
        serial_args = [self.blob(a) if isinstance(a, bytes) else a for a in args]
        result, error = None, 0
        try:
            result = super().__call__(op, *args)
            return result
        except OSError as exc:
            error = exc.errno or errno.EIO
            raise FuseOSError(error)
        except Exception:
            error = errno.EIO
            raise
        finally:
            self.seq += 1
            recorded = {"len": len(result), "sha256": hashlib.sha256(result).hexdigest()} if isinstance(result, bytes) else result
            self.log.write(json.dumps({"seq": self.seq, "op": op, "args": serial_args, "result": recorded,
                "errno": error, "pid": pid, "uid": uid, "gid": gid, "start_ns": started,
                "end_ns": time.monotonic_ns()}, ensure_ascii=True) + "\n")
            # Logs are outside the measured root; do not silently lose recording errors.

    def init(self, path): return None
    def destroy(self, path):
        for fd in self.handles.values(): os.close(fd)
        self.handles.clear()
        return None
    def getattr(self, path, fh=None):
        s = os.fstat(self.handles[fh]) if fh in self.handles else os.lstat(self.path(path))
        return {k: getattr(s, k) for k in ("st_mode", "st_ino", "st_dev", "st_nlink", "st_uid", "st_gid", "st_size", "st_atime", "st_mtime", "st_ctime")}
    def open(self, path, flags): return self.alloc(os.open(self.path(path), flags))
    def create(self, path, mode, fi=None): return self.alloc(os.open(self.path(path), os.O_CREAT | os.O_RDWR | os.O_TRUNC, mode))
    def read(self, path, size, offset, fh): return os.pread(self.handles[fh], size, offset)
    def write(self, path, data, offset, fh): return os.pwrite(self.handles[fh], data, offset)
    def release(self, path, fh): os.close(self.handles.pop(fh)); return 0
    def flush(self, path, fh): os.close(os.dup(self.handles[fh])); return 0
    def fsync(self, path, datasync, fh):
        (os.fdatasync if datasync else os.fsync)(self.handles[fh]); return 0
    def opendir(self, path): return self.alloc(os.open(self.path(path), os.O_RDONLY | os.O_DIRECTORY))
    def readdir(self, path, fh): return [".", ".."] + sorted(os.listdir(self.handles[fh]))
    def releasedir(self, path, fh): return self.release(path, fh)
    def fsyncdir(self, path, datasync, fh): return self.fsync(path, datasync, fh)
    def truncate(self, path, length, fh=None):
        if fh in self.handles: os.ftruncate(self.handles[fh], length)
        else: os.truncate(self.path(path), length)
        return 0
    def mkdir(self, path, mode): os.mkdir(self.path(path), mode); return 0
    def rmdir(self, path): os.rmdir(self.path(path)); return 0
    def unlink(self, path): os.unlink(self.path(path)); return 0
    def rename(self, old, new): os.rename(self.path(old), self.path(new)); return 0
    def chmod(self, path, mode): os.chmod(self.path(path), mode); return 0
    def chown(self, path, uid, gid): os.chown(self.path(path), uid, gid); return 0
    def utimens(self, path, times=None): os.utime(self.path(path), times); return 0
    def readlink(self, path): return os.readlink(self.path(path))
    def symlink(self, target, source): os.symlink(source, self.path(target)); return 0
    def link(self, target, source): os.link(self.path(source), self.path(target)); return 0
    def access(self, path, amode):
        if not os.access(self.path(path), amode): raise FuseOSError(errno.EACCES)
        return 0
    def getxattr(self, path, name, position=0): return os.getxattr(self.path(path), name, follow_symlinks=False)
    def listxattr(self, path): return os.listxattr(self.path(path), follow_symlinks=False)
    def setxattr(self, path, name, value, options, position=0): os.setxattr(self.path(path), name, value, options, follow_symlinks=False); return 0
    def removexattr(self, path, name): os.removexattr(self.path(path), name, follow_symlinks=False); return 0
    def statfs(self, path):
        s = os.statvfs(self.path(path))
        return {k: getattr(s, k) for k in ("f_bsize", "f_frsize", "f_blocks", "f_bfree", "f_bavail", "f_files", "f_ffree", "f_favail", "f_flag", "f_namemax")}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--mount", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    FUSE(Recorder(a.root, a.output), str(a.mount), foreground=True, nothreads=True,
         allow_other=True, default_permissions=True, attr_timeout=0, entry_timeout=0,
         negative_timeout=0, fsname="ax-full-trace")
