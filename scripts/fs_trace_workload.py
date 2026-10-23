"""Fixed code-inspection/edit/validation tool step from pytest-10356; no model.

Only source workspace I/O is the subject. AST parsing does not replace benchmark
tests. The patch is the previously recorded agent prediction, not the gold patch.
"""
import ast
import json
import os
import sys


def read(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        chunks = []
        while True:
            data = os.read(fd, 32768)
            if not data: return b"".join(chunks)
            chunks.append(data)
    finally:
        os.close(fd)


def write(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        for offset in range(0, len(data), 4096):
            chunk = data[offset:offset+4096]
            if os.write(fd, chunk) != len(chunk): raise RuntimeError("Short write")
        os.fsync(fd)
    finally:
        os.close(fd)


def probe():
    fd = os.open("probe.bin", os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o644)
    assert os.write(fd, b"abcd") == 4
    assert os.lseek(fd, 0, os.SEEK_SET) == 0
    assert os.read(fd, 6) == b"abcd"  # short read: count 4, not requested 6
    assert os.pread(fd, 2, 1) == b"bc"  # 2 repeated bytes
    assert os.pread(fd, 2, 9) == b""  # EOF is not read traffic
    assert os.pwrite(fd, b"Z", 1) == 1  # invalidate only overwritten byte
    assert os.pread(fd, 2, 1) == b"Zc"  # 1 repeated byte
    os.fsync(fd)
    os.rename("probe.bin", "renamed.bin")
    assert os.pread(fd, 2, 1) == b"Zc"  # identity survives rename: 2 repeats
    os.close(fd)
    try:
        os.open("absent", os.O_RDONLY)
        raise AssertionError("Expected ENOENT")
    except FileNotFoundError:
        pass
    print(json.dumps({"expected_read_bytes": 10, "expected_write_bytes": 5,
                      "expected_repeat_bytes": 5, "expected_repeat_ratio": 0.5}))


def task():
    source = "src/_pytest/mark/structures.py"
    tests = "testing/test_mark.py"
    inspection = read(source)
    test_source = read(tests)
    assert b"get_unpacked_marks" in inspection
    ast.parse(test_source)
    # A separate edit step reads its current input again, as a tool would.
    original = read(source).decode()
    before = '''def get_unpacked_marks(obj: object) -> Iterable[Mark]:
    """Obtain the unpacked marks that are stored on an object."""'''
    after = '''def get_unpacked_marks(
    obj: object, *, consider_mro: bool = True
) -> Iterable[Mark]:
    """Obtain the unpacked marks that are stored on an object.

    If obj is a class and consider_mro is true, return marks applied to
    this class and all of its super-classes in MRO order. If consider_mro
    is false, only return marks applied directly to this class.
    """
    if isinstance(obj, type):
        if not consider_mro:
            mark_lists = [obj.__dict__.get("pytestmark", [])]
        else:
            mark_lists = [x.__dict__.get("pytestmark", []) for x in obj.__mro__]
        return [
            mark
            for item in mark_lists
            for mark in normalize_mark_list(item if isinstance(item, list) else [item])
        ]'''
    old = "obj.pytestmark = [*get_unpacked_marks(obj), mark]"
    new = "obj.pytestmark = [*get_unpacked_marks(obj, consider_mro=False), mark]"
    assert original.count(before) == original.count(old) == 1
    changed = original.replace(before, after).replace(old, new).encode()
    write(source + ".tmp", changed)
    os.rename(source + ".tmp", source)
    validated = read(source)
    assert validated == changed
    ast.parse(validated)
    print(json.dumps({"task": "pytest-dev__pytest-10356", "validation": "AST and exact written bytes",
                      "source_bytes_before": len(inspection), "source_bytes_after": len(validated),
                      "test_source_bytes": len(test_source)}))


if __name__ == "__main__":
    probe() if sys.argv[1] == "probe" else task()
