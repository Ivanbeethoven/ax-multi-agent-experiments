import copy
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from bench.runtime import fork_release as release

SCRIPT = (Path(__file__).resolve().parents[1] / 'experiments/unified-suite/pilot-aliyun'
          / 'scripts/draft-c4-native-100k.py')
MODULE = importlib.util.spec_from_file_location('draft_c4_native_100k', SCRIPT)
draft = importlib.util.module_from_spec(MODULE)
MODULE.loader.exec_module(draft)


class C4ReleaseDraftTest(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.binary = self.root / 'brewfs'
        self.binary.write_bytes(b'optimized test fixture, never executed')
        self.bin_ref = draft.ref(self.binary)
        invocation = 'f' * 32
        self.build = dict(
            schema=release.BUILD, outcome='verified-release-artifact', invocation_id=invocation,
            unit='ax-brewfs-c4-native-fork-fixture-release-03.service',
            source_commit=release.SOURCE, source_tree=release.TREE, patch_sha256=release.PATCH,
            prior_opt0_sha256=release.OPT0, profile=copy.copy(release.PROFILE),
            terminal_unit=['LoadState=loaded', f'InvocationID={invocation}', 'ActiveState=active',
                           'SubState=exited', 'Result=success', 'ExecMainCode=1',
                           'ExecMainStatus=0', 'MainPID=0'],
            logs_sha256={key: 'a' * 64 for key in release.LOGS},
            artifact=dict(remote_path='/home/dev/build/brewfs-linux-x86_64',
                          sha256=self.bin_ref['sha256'], bytes=self.binary.stat().st_size))
        self.review_ref = self.review(self.build)

    def document(self, name, value):
        path = self.root / name
        path.write_text(json.dumps(value), encoding='utf-8')
        return draft.ref(path)

    def review(self, build, reviewed=True):
        self.build_ref = self.document('build-receipt.json', build)
        return self.document('review.json', dict(schema=release.REVIEW, reviewed=reviewed,
                                                 binary=self.bin_ref, build_receipt=self.build_ref))

    def args(self, ref=None, **kwargs):
        chosen = self.review_ref if ref is None else ref
        return SimpleNamespace(release_review=chosen['path'], release_review_sha256=chosen['sha256'],
                               binary=str(self.binary), **kwargs)

    def test_release_requires_review_and_never_falls_back_to_opt0(self):
        binding = dict(review=self.review_ref, build_receipt=self.build_ref)
        self.assertEqual(draft.engine(self.args(), 'brewfs-fork'), (self.bin_ref, binding))
        with self.assertRaisesRegex(ValueError, 'requires release review'):
            draft.engine(SimpleNamespace(binary=str(self.binary)), 'brewfs-fork')
        with self.assertRaisesRegex(ValueError, 'Both release review'):
            draft.engine(SimpleNamespace(binary=str(self.binary), release_review='review.json'), 'brewfs-fork')
        with self.assertRaisesRegex(ValueError, 'Human-reviewed'):
            draft.engine(self.args(self.review(self.build, reviewed=False)), 'brewfs-fork')
        # The existing pinned diagnostic route retains its two original fields.
        opt0 = dict(self.bin_ref, sha256=release.OPT0)
        self.assertIsNone(draft.release_binding(SimpleNamespace(), 'brewfs-fork', opt0))

    def test_review_rejects_wrong_build_profile_lineage_and_artifact(self):
        cases = [
            ('source_tree', 'b' * 40, 'source, tree, patch'),
            ('source_commit', 'b' * 40, 'source, tree, patch'),
            ('patch_sha256', 'b' * 64, 'source, tree, patch'),
            ('profile', dict(release.PROFILE, opt_level=0), 'optimized release'),
            ('terminal_unit', self.build['terminal_unit'] + ['Result=timeout'], 'exited successfully'),
            ('artifact', dict(self.build['artifact'], sha256=release.OPT0), 'SHA-256 or size'),
            ('artifact', dict(self.build['artifact'], bytes=1), 'SHA-256 or size'),
        ]
        for key, value, error in cases:
            with self.subTest(key=key, value=value), self.assertRaisesRegex(ValueError, error):
                draft.engine(self.args(self.review(dict(self.build, **{key: value}))), 'brewfs-fork')
        ref = self.review(self.build)
        Path(self.build_ref['path']).write_text('{}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'changed or non-absolute pinned file'):
            draft.engine(self.args(ref), 'brewfs-fork')
        ref = self.review(self.build)
        self.binary.write_bytes(b'transferred different executable bytes')
        with self.assertRaisesRegex(ValueError, 'this installed executable'):
            draft.engine(self.args(ref), 'brewfs-fork')

    def test_source_and_burst_bind_same_receipt_and_reviewed_child(self):
        binding = dict(review=self.review_ref, build_receipt=self.build_ref)
        tree = self.document('tree.json', {'f': 'fixture'})
        ready = self.document('ready.json', {'spec': self.document('mount.json', {
            'schema': 'experiment-mount-v1', 'reviewed': True})})
        mount = json.loads(Path(ready['path']).read_text())['spec']
        config = self.document('data.json', {})
        revision = f'11111111-1111-1111-1111-111111111111:1:{"0" * 64}'
        publication = self.document('publication.json', {'fork_source': revision})
        carrier = self.document('carrier.json', dict(schema='experiment-step-v1', reviewed=True, inputs=[]))
        for name in ('stdout.log', 'finished.json'):
            self.document(name, {})
        source_args = self.args(method='brewfs-fork', cell='cell-fixture', tree=tree['path'],
                                mount_spec=mount['path'], ready=ready['path'], publication=publication['path'],
                                redis_url='redis://127.0.0.1:6379/1', namespace='fixture', data_config=config['path'],
                                carrier_step=carrier['path'], carrier_output=str(self.root))
        stopped = self.document('stopped.json', {})
        with patch.object(draft, 'TREE_SHA', tree['sha256']), patch.object(draft, 'preflight') as preflight:
            with self.assertRaisesRegex(ValueError, 'same release provenance'):
                draft.source(source_args)
            self.document('carrier.json', dict(schema='experiment-step-v1', reviewed=True, inputs=list(binding.values())))
            source = draft.source(source_args)
            self.assertIs(source['reviewed'], False)
            self.assertEqual(source['release_candidate'], binding)
            source_ref = self.document('source-reviewed.json', dict(source, reviewed=True))
            qualified = self.document('qualified.json', dict(schema=draft.QUALIFIED, request=source_ref,
                                                           files=100000, bytes=409600000))
            timing = dict(qualification=qualified['path'], probe_stopped=stopped['path'],
                          measurement_spec=None, measurement_ready=None)
            with self.assertRaisesRegex(ValueError, 'requires release review'):
                draft.measured(SimpleNamespace(**timing))
            with self.assertRaisesRegex(ValueError, 'qualification does not bind'):
                changed = self.review(dict(self.build, invocation_id='a' * 32,
                    terminal_unit=[line.replace('f' * 32, 'a' * 32) for line in self.build['terminal_unit']]))
                draft.measured(self.args(changed, **timing))
            # Restore the exact reviewed bytes after testing a different otherwise valid receipt.
            self.review(self.build)
            measured = draft.measured(self.args(**timing))
            self.assertEqual(measured['release_candidate'], binding)
            child = dict(schema='experiment-step-v1', reviewed=True, stage='fork-child-01', cell_id='cell-fixture',
                         argv=draft.native_argv(measured, 'child-01'), cwd=str(self.root),
                         executable_sha256=self.bin_ref['sha256'], inputs=[tree, config])
            child_ref = self.document('child-reviewed.json', child)
            out = self.root / 'burst-draft.json'
            cli = [str(SCRIPT), 'burst', '--qualification', qualified['path'], '--probe-stopped', stopped['path'],
                   '--release-review', self.review_ref['path'], '--release-review-sha256', self.review_ref['sha256'],
                   '--child', 'child-01', '--step', child_ref['path'], '--out', str(out)]
            with patch.object(sys, 'argv', cli), redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(ValueError, 'same release provenance'):
                    draft.main()
                self.assertFalse(out.exists())
                child_ref = self.document('child-reviewed.json', dict(child, inputs=[tree, config, *binding.values()]))
                draft.main()
            result = json.loads(out.read_text())
            self.assertIs(result['reviewed'], False)
            self.assertEqual(result['release_candidate'], binding)
            self.assertEqual(result['children'], [{'id': 'child-01', 'step': child_ref}])
            self.assertEqual(preflight.call_count, 3)


if __name__ == '__main__':
    unittest.main()
