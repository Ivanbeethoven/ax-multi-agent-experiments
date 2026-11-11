"""A saved version check must not admit a stale or mismatched product build."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import runpy
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
TOOL = runpy.run_path(str(REPO / 'experiments/unified-suite/pilot-aliyun/scripts/continue-flat-c3-after-base.py'))


class IndependentLatest(unittest.TestCase):
    def test_a_valid_receipt_is_bound_to_the_manifest_and_exact_current_tip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / 'build.json'
            receipt = root / 'latest.json'
            sources = [dict(component='brewfs', repository='https://github.com/brewfs/brewfs.git', branch='main', source_commit='a'*40),
                       dict(component='rustfs', repository='https://github.com/Ivanbeethoven/rustfs.git', branch='feat/dynamic-block-size-rebased', source_commit='b'*40)]
            manifest.write_text(json.dumps(dict(sources=sources)))
            value = dict(passed=True, at=datetime.now(timezone.utc).isoformat(), build_manifest_sha256=TOOL['digest'](manifest),
                         sources=[dict(component=s['component'], repository=s['repository'].removesuffix('.git'), branch=s['branch'],
                                       built_commit=s['source_commit'], remote_commit=s['source_commit'], lookup_exit_code=0, matches=True) for s in sources])

            def persist():
                receipt.write_text(json.dumps(value))
                return TOOL['ref'](receipt)

            pin = persist()
            self.assertEqual(TOOL['latest_receipt'](pin, manifest), receipt)
            value['sources'][1]['remote_commit'] = 'c'*40
            with self.assertRaisesRegex(ValueError, 'tip differs'):
                TOOL['latest_receipt'](persist(), manifest)
            value['sources'][1]['remote_commit'] = 'b'*40
            value['at'] = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
            with self.assertRaisesRegex(ValueError, 'current branch receipt'):
                TOOL['latest_receipt'](persist(), manifest)
            value['at'] = datetime.now(timezone.utc).isoformat()
            pin = persist()
            manifest.write_text(manifest.read_text() + '\n')
            with self.assertRaisesRegex(ValueError, 'exact build'):
                TOOL['latest_receipt'](pin, manifest)


if __name__ == '__main__':
    unittest.main()
