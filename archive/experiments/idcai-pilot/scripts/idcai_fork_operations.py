"""The same six child mutations for C4 BrewFS fork and JuiceFS clone."""
import hashlib
import os
from pathlib import Path
import sys


def digest(data):
    return hashlib.sha256(data).hexdigest()


def prepare_operations(source, expected):
    names = sorted(expected)[:5]
    if len(names) != 5 or any(name in expected for name in ('child.txt', 'child-renamed.txt')):
        raise ValueError('C4 requires five base files and unused child output names')
    original = {name: (source/name).read_bytes() for name in names}
    if any(digest(data) != expected[name] for name, data in original.items()):
        raise ValueError('C4 mutation inputs differ from the frozen parent')
    overwrite, append, truncate, unlink, rename = names
    written = {
        overwrite: b'child overwrite\n',
        append: original[append] + b'child append\n',
        truncate: original[truncate][:31],
        'child.txt': b'child new file\n',
    }
    after = dict(expected)
    after.update({name: digest(data) for name, data in written.items()})
    del after[unlink]
    after['child-renamed.txt'] = after.pop(rename)
    return dict(names=names, expected_after=after, written_paths=list(written))


def apply_operations(root, plan):
    overwrite, append, truncate, unlink, rename = plan['names']
    operations = []
    for kind, name, mode, payload in (
        ('overwrite', overwrite, 'wb', b'child overwrite\n'),
        ('append', append, 'ab', b'child append\n'),
        ('truncate', truncate, 'r+b', None),
        ('new-file', 'child.txt', 'xb', b'child new file\n'),
    ):
        with (root/name).open(mode) as stream:
            if kind == 'truncate':
                stream.truncate(31)
            else:
                stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        operations.append(dict(operation=kind, path=name, fsync=True))
    (root/unlink).unlink()
    operations.append(dict(operation='unlink', path=unlink))
    if (root/'child-renamed.txt').exists():
        raise ValueError('Rename destination already exists')
    (root/rename).rename(root/'child-renamed.txt')
    operations.append(dict(operation='rename', path=rename, destination='child-renamed.txt'))
    return operations


def adapt_native(text):
    begin = text.index('            changed = {min(expected):')
    end = text.index("            visit(product,point,out,command('child-write-", begin)
    return text[:begin] + '''            from idcai_fork_operations import prepare_operations, apply_operations
            plan = prepare_operations(source, expected)
            changed = plan['written_paths']
            expected_after = plan['expected_after']
            def modify_child():
                arm['child_before'] = tree(child_path)
                arm['inherited_all'] = arm['child_before'] == expected
                if not arm['inherited_all']:
                    raise RuntimeError('Child does not inherit the full parent before writing')
                arm['child_operations'] = apply_operations(child_path, plan)
                arm['child_after_write'] = tree(child_path)
                if arm['child_after_write'] != expected_after:
                    raise RuntimeError('Child tree differs after the six C4 mutations')
''' + text[end:]


if __name__ == '__main__':
    path = Path(sys.argv[1])
    updated = adapt_native(path.read_text())
    compile(updated, str(path), 'exec')
    path.write_text(updated)
