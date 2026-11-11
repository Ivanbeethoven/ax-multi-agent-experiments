#!/usr/bin/env python3
"""Restore the complete fixed checkpoint and check invalid inputs; no model call."""
import argparse
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile

TOOL = Path(__file__).with_name('package-session-checkpoint.py')
spec = importlib.util.spec_from_file_location('checkpoint_package', TOOL)
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--checkpoint', type=Path, required=True)
    p.add_argument('--package', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    receipt = a.package/'receipt.json'
    archive = a.package/'checkpoint.tar.gz'
    receipt_sha = package.digest(receipt)
    original, source_tree = package.validate_source(a.checkpoint)
    a.out.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix='checkpoint-check-') as temporary:
        root = Path(temporary)
        restored = package.install(archive, receipt, receipt_sha, root/'restored')
        _, actual_tree = package.validate_source(root/'restored')
        if source_tree != actual_tree:
            raise ValueError('Complete restored tree differs')

        def rejected(operation, absent):
            try:
                operation()
            except ValueError:
                if absent.exists():
                    raise ValueError('Invalid input created an extraction/package directory')
                return True
            raise ValueError('Invalid input was accepted')

        corrupt = root/'corrupt.tar.gz'
        raw = bytearray(archive.read_bytes());raw[len(raw)//2] ^= 1
        corrupt.write_bytes(raw)
        corrupt_rejected = rejected(lambda: package.install(corrupt, receipt, receipt_sha, root/'bad-bytes'), root/'bad-bytes')
        wrong_reader = json.loads(receipt.read_text())
        wrong_reader['tree_reader_sha256'] = '0'*64
        reader_receipt = root/'wrong-reader.json'
        package.write(reader_receipt, wrong_reader)
        reader_rejected = rejected(lambda: package.install(archive, reader_receipt, package.digest(reader_receipt), root/'bad-reader'), root/'bad-reader')
        active = root/'active'
        shutil.copytree(a.checkpoint, active, symlinks=True)
        config = active/'workspace/config/opencode.json'
        value = json.loads(config.read_text())
        value['provider']['experiment']['options']['apiKey'] = 'test-active-relay-key'
        config.write_text(json.dumps(value))
        active_rejected = rejected(lambda: package.pack(active, root/'bad-active'), root/'bad-active')
    result = dict(passed=True, at=datetime.now(timezone.utc).isoformat(),
        restored_files=restored['files'], complete_tree_equal=True,
        corruption_rejected_before_extraction=corrupt_rejected,
        changed_tree_reader_rejected_before_extraction=reader_rejected,
        active_provider_configuration_rejected_before_packaging=active_rejected,
        archive_sha256=package.digest(archive), receipt_sha256=receipt_sha,
        checkpoint_sha256=package.digest(a.checkpoint/'checkpoint.json'),
        session_id=original['session_id'], package_tool_sha256=package.digest(TOOL),
        check_tool_sha256=package.digest(Path(__file__)), model_calls=0,
        scope='Local complete input packaging/restoration; no cloud/FUSE/performance result')
    package.write(a.out/'result.json', result)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
