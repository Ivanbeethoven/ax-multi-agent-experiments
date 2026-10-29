import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'bench'))
import trace_checks as checks


class TraceChecks(unittest.TestCase):
    def save(self,path,value):
        path.write_text(json.dumps(value));return path

    def test_input_inventory_requires_final_tree_and_rejects_changed_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);case=root/'case';(case/'capture/blobs').mkdir(parents=True);(case/'initial').mkdir()
            files=['capture/events.jsonl','metrics.json','initial-tree.json','final-tree.json','capture/blobs/payload']
            for name in files:(case/name).write_text('{}')
            inventory={name:checks.digest(case/name) for name in files}
            manifest=self.save(root/'manifest.json',inventory)
            self.assertTrue(checks.inputs(case,manifest,checks.digest(manifest))['passed'])
            (case/'capture/blobs/payload').write_text('changed')
            with self.assertRaises(ValueError):checks.inputs(case,manifest,checks.digest(manifest))
            del inventory['final-tree.json'];self.save(manifest,inventory)
            with self.assertRaises(ValueError):checks.inputs(case,manifest,checks.digest(manifest))

    def test_manifest_cannot_include_external_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);case=root/'case';case.mkdir()
            outside=root/'outside';outside.write_text('outside')
            value={k:checks.digest(outside) for k in ['capture/events.jsonl','metrics.json','initial-tree.json','final-tree.json']}
            value={'../outside':checks.digest(outside),**value}
            manifest=self.save(root/'manifest.json',value)
            with self.assertRaises(ValueError):checks.inputs(case,manifest,checks.digest(manifest))

    def test_count_pass_cannot_replace_tree_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);target=root/'tree';target.mkdir();(target/'f').write_text('expected')
            expected=self.save(root/'expected.json',checks.tree(target))
            metrics=self.save(root/'metrics.json',{'callbacks':2,'actual_read_bytes':8,'actual_write_bytes':0})
            result=self.save(root/'replay.json',{'completed':True,'remaining_handles':0,'operations':2,
                                              'actual_read_bytes':8,'actual_write_bytes':0})
            self.assertFalse(checks.replay(result,metrics)['strict_correctness'])
            self.assertTrue(checks.content(target,expected)['passed'])
            (target/'f').write_text('corrupted')
            with self.assertRaises(ValueError):checks.content(target,expected)
            self.save(result,{'completed':True,'remaining_handles':1,'operations':2,'actual_read_bytes':8,'actual_write_bytes':0})
            with self.assertRaises(ValueError):checks.replay(result,metrics)


if __name__=='__main__':unittest.main()
