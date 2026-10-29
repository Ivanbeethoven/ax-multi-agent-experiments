"""Small provider-independent primitives; no deployment or process side effects."""
import hashlib
import json
from pathlib import Path, PurePosixPath


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write_new(path, value):
    with Path(path).open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def linux_path(value):
    if not isinstance(value, str) or any(c in value for c in '\\\n\r\0'):
        raise ValueError('absolute Linux path required')
    p = PurePosixPath(value)
    if p.anchor != '/' or str(p) == '/' or '..' in p.parts:
        raise ValueError('non-root absolute Linux path without traversal required')
    return p


def contained(root, relative):
    p = Path(relative)
    if not relative or p.is_absolute() or '..' in p.parts:
        raise ValueError('relative path without traversal required')
    root = Path(root).resolve(strict=True)
    target = (root / p).resolve(strict=True)
    if not target.is_relative_to(root):
        raise ValueError('path escapes approved root')
    return target
