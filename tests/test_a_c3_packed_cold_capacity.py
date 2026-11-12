import hashlib
import json
from pathlib import Path
import runpy
import unittest


PILOT = Path(__file__).resolve().parents[1] / 'experiments/unified-suite/pilot-aliyun'
PROBE = runpy.run_path(str(PILOT / 'scripts/a-c3-packed-cold-capacity.py'))


class ColdObjectsCapacityTest(unittest.TestCase):
    def test_selects_exact_original_unmounted_devices_and_checks_historical_growth(self):
        original = (PILOT / 'evidence/preparation/packed-live-batch-01/capacity/result.json').read_bytes()
        self.assertEqual(hashlib.sha256(original).hexdigest(), PROBE['HISTORICAL_SHA256'])
        saved = json.loads(original)
        rows = [dict(path=f'/dev/vd{index}', type='disk', serial=serial, size=60 * 1024**3,
                     uuid=f'fs-{index}', fstype='ext4', mountpoints=[None],
                     **{'maj:min': f'253:{index}'}) for index, serial in enumerate(PROBE['SERIALS'])]
        scratch = dict(rows[0], path='/dev/vds', serial='bp16jaiu2fcmbij8mg7i', **{'maj:min': '253:17'})
        self.assertEqual(PROBE['select_disks']([scratch, *reversed(rows)], set()), rows)
        for idx in range(4):
            disk = f'/srv/ax-objects-{idx}'
            source = {row['quantity']: row for row in saved['rows'] if row.get('path') == disk}
            out = PROBE['diagnostic'](idx, [source[key]['total'] for key in ('bytes', 'inodes')],
                                      [source[key]['after_available'] for key in ('bytes', 'inodes')])
            for key in ('bytes', 'inodes'):
                self.assertEqual(out['seven_more'][key]['historical_increment'],
                                 source[key]['observed_window_increment'])
                self.assertEqual(out['new_window_plus_seven_more'][key]['minimum']
                                 - out['seven_more'][key]['minimum'], source[key]['observed_window_increment'])
        self.assertEqual(PROBE['diagnostic'](0, (60070604800, 15728640), (29249671168, 10000000))
                         ['new_window_plus_seven_more']['bytes']['margin'], 8052318208)
        with self.assertRaisesRegex(ValueError, 'Expected unmounted'):
            PROBE['select_disks'](rows, {'253:0'})
        with self.assertRaisesRegex(ValueError, 'Expected unmounted'):
            PROBE['select_disks']([dict(rows[0], mountpoints=['/srv/ax-objects-0']), *rows[1:]], set())
        with self.assertRaisesRegex(ValueError, 'Missing or ambiguous'):
            PROBE['select_disks']([rows[0], *rows], set())
        with self.assertRaisesRegex(ValueError, 'Missing or ambiguous'):
            PROBE['select_disks']([scratch, *rows[1:]], set())
        with self.assertRaisesRegex(ValueError, 'Repeated disk uuid'):
            PROBE['select_disks']([dict(rows[0], uuid=rows[1]['uuid']), *rows[1:]], set())


if __name__ == '__main__':
    unittest.main()
