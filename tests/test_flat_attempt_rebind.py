"""Only plan/attempt metadata may change when reusing old base/calibration."""
import copy
from pathlib import Path
import runpy
import unittest

from bench.suite_catalog import template,select_pilot_files
from bench.suite_plan import build

REPO=Path(__file__).resolve().parents[1]
CODE=runpy.run_path(str(REPO/'experiments/unified-suite/pilot-aliyun/scripts/rebind-flat-attempt.py'))


class FlatAttemptRebind(unittest.TestCase):
    def test_new_campaign_preserves_the_entire_cell_and_old_plan(self):
        old=build(select_pilot_files(template(),100000));original=copy.deepcopy(old)
        cell=next(c for c in old['cells'] if c['workload']=='smallfiles' and c['arm_id']=='juicefs')
        new,current=CODE['rebound_plan'](old,cell['id'],'explicit-new-attempt')
        self.assertEqual(old,original);self.assertEqual(current,cell);self.assertNotEqual(new['plan_sha256'],old['plan_sha256'])
        self.assertEqual(len(new['config']['cases']),1)
        self.assertEqual(new['config']['seed'],old['config']['seed']);self.assertEqual(current['asset_id'],cell['asset_id'])

    def test_asset_view_keeps_actual_prepared_plan_base_config_and_binary(self):
        old=dict(plan={'path':'/old/plan','sha256':'a'*64},directory='/old/asset',mount_point='/old/mnt',
            config={'metadata':'same-private-endpoint','cache_mib':512},bucket='same-bucket',database=60,seed=1,filesystem_binary={'sha256':'b'*64})
        unchanged=copy.deepcopy(old);prepared={'path':'/old/asset/asset.json','sha256':'c'*64};newplan={'path':'/new/plan','sha256':'d'*64}
        view=CODE['asset_view'](old,prepared,newplan,'/new/asset',{'path':'/new/rebind','sha256':'e'*64})
        self.assertEqual(old,unchanged);self.assertEqual(view['prepared_plan'],old['plan']);self.assertEqual(view['prepared_asset'],prepared)
        self.assertEqual(view['plan'],newplan);self.assertEqual(view['mount_point'],'/new/asset/mnt')
        for key in ('config','bucket','database','seed','filesystem_binary'):self.assertEqual(view[key],old[key])


if __name__=='__main__':unittest.main()
