"""Small regressions for cold-check scopes; no FUSE, model, or cloud needed."""
import importlib.util
from pathlib import Path
import shutil
import tempfile
import unittest

from bench.runtime import c4_verify as verify


def fixture(root):
    root.mkdir();(root/'d').mkdir()
    (root/'d/f').write_bytes(b'parent')
    (root/'d/old').write_bytes(b'rename')
    (root/'same').write_bytes(b'untouched')


class C4CalibrationVerify(unittest.TestCase):
    def test_full_reference_checks_all_and_reduced_reads_only_touched_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent=Path(tmp)/'parent';fixture(parent);child=Path(tmp)/'child';shutil.copytree(parent,child)
            (child/'d/f').write_bytes(b'child!');(child/'d/old').rename(child/'new')
            original,_,_=verify.scan(parent);expected,_,_=verify.scan(child)
            full=verify.check(parent,child,original,expected,first_read='d/f')
            self.assertTrue(full['passed']);self.assertEqual(full['content_read']['child']['files'],3)
            args=dict(first_read='d/f',reference=full['parent_identity'])
            reduced=verify.check(parent,child,original,expected,**args)
            self.assertTrue(reduced['passed']);self.assertTrue(reduced['parent_identity_unchanged'])
            self.assertEqual(reduced['touched_paths'],['d/f','d/old','new'])
            self.assertEqual(reduced['content_read']['child'],dict(files=2,bytes=12))
            (child/'d/f').write_bytes(b'broken')
            self.assertFalse(verify.check(parent,child,original,expected,**args)['passed'])
            (child/'d/f').write_bytes(b'child!');(child/'same').unlink()
            self.assertFalse(verify.check(parent,child,original,expected,**args)['passed'])

    def test_reduced_detects_parent_replacement_even_when_other_bytes_not_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent=Path(tmp)/'parent';fixture(parent);child=Path(tmp)/'child';shutil.copytree(parent,child)
            (child/'d/f').write_bytes(b'child!')
            original,_,_=verify.scan(parent);expected,_,_=verify.scan(child)
            baseline=verify.check(parent,child,original,expected,first_read='d/f')
            (parent/'same').rename(Path(tmp)/'held');(parent/'same').write_bytes(b'untouched')
            observed=verify.check(parent,child,original,expected,first_read='d/f',reference=baseline['parent_identity'])
            self.assertTrue(observed['parent_matches'])
            self.assertFalse(observed['parent_identity_unchanged']);self.assertFalse(observed['passed'])

    def test_reference_binding_rejects_other_asset_or_same_attempt(self):
        binding=dict(arm='juicefs',asset='base-1')
        identity=dict(schema='c4-parent-stat-v1',entries=5,sha256='a'*64)
        original=dict(verification_policy=verify.REFERENCE,passed=True,full_cold_child=True,parent_unchanged=True,
                      child_name='first',binding=binding,parent_identity=identity)
        self.assertEqual(verify.reference_identity(original,binding,'later'),identity)
        for wanted,name in [(binding,'first'),({**binding,'arm':'native'},'later'),({**binding,'asset':'base-2'},'later')]:
            with self.assertRaises(ValueError):verify.reference_identity(original,wanted,name)

    def test_formal_rejects_any_calibration_scope(self):
        for policy,ref in [(verify.TOUCHED,{'sha256':'a'*64}),(verify.REFERENCE,None)]:
            with self.assertRaises(ValueError):verify.scope(policy,'experiment',ref)
        verify.scope(verify.FULL,'experiment',None)
        with self.assertRaises(ValueError):verify.scope(verify.TOUCHED,'observer-calibration-on',None)

    def test_batch_freezes_first_trial_full_and_later_trials_reference_bound(self):
        source=Path(__file__).resolve().parents[1]/'experiments/unified-suite/pilot-aliyun/scripts/run-c4-batch.py'
        spec=importlib.util.spec_from_file_location('c4_batch_policy',source)
        mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
        self.assertEqual(mod.trial_verification(verify.FULL,5,None),{})
        self.assertEqual(mod.trial_verification(verify.TOUCHED,0,None),{'verification_policy':verify.REFERENCE})
        with self.assertRaises(ValueError):mod.trial_verification(verify.TOUCHED,1,None)
        original=dict(path='/original',sha256='a'*64)
        self.assertEqual(mod.trial_verification(verify.TOUCHED,1,original),
                         {'verification_policy':verify.TOUCHED,'full_reference':original})
