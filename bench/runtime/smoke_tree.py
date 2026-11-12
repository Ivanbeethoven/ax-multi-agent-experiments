import hashlib
import os
from pathlib import Path

def _walk_error(error):
    # os.walk otherwise swallows OSError, including a deadline's TimeoutError.
    raise error

def file_tree(root):
    import stat
    entries = {}
    paths = []
    for directory, dirs, files in os.walk(root, followlinks=False, onerror=_walk_error):
        if Path(directory) == root:
            dirs[:] = [name for name in dirs if name not in ('.trash',)]
            files = [name for name in files if name not in ('.stats','.config','.accesslog')]
        paths.extend(Path(directory)/name for name in dirs+files)
    for path in sorted(paths):
        key = str(path.relative_to(root))
        if key.split('/')[0] in ('.stats', '.config', '.accesslog', '.trash'):
            continue
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            value = {'type':'link','target':os.readlink(path)}
        elif stat.S_ISDIR(info.st_mode):
            value = {'type':'directory'}
        else:
            value = {'type':'file','bytes':info.st_size,
                     'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
        value['mode'] = info.st_mode & 0o7777
        entries[key] = value
    return entries
