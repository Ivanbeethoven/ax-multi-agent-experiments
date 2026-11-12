from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import UUID

PILOT = Path(__file__).resolve().parents[1]/'experiments/unified-suite/pilot-aliyun'
MODULE = importlib.util.spec_from_file_location('c3_packed_reboot_evidence', PILOT/'scripts/c3-packed-reboot-evidence.py')
CHECK = importlib.util.module_from_spec(MODULE)
MODULE.loader.exec_module(CHECK)

NOW = datetime(2026, 11, 12, 4, 5, tzinfo=timezone.utc)
WAKE = '2026-11-12T04:00:00+00:00'
AT = '2026-11-12T04:04:00+00:00'
STOP = PILOT/'evidence/preparation/c4-observer-fold-real-03/historical-objects-pack/cloud-stopped.json'


def ref(path):
    return dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())


class TestPackedRebootEvidence(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.old, self.details = CHECK.historical()
        self.instances, self.disks, self.native = [], [], {}
        for j, role in enumerate(CHECK.ROLES):
            old, details = self.old[role], self.details[role]
            addresses = CHECK.global_addresses(old)
            ip = next(a for a in addresses if a.startswith('10.20.2.'))
            shape = details['api']
            self.instances.append(dict(InstanceId=old['instance_id'], HostName=old['hostname'],
                Status='Running', PublicIpAddress=dict(IpAddress=[]), EipAddress=dict(IpAddress=''),
                VpcAttributes=dict(VpcId=shape['vpc'], PrivateIpAddress=dict(IpAddress=[ip])),
                **{k:shape[k] for k in ('InstanceType','Cpu','Memory','ZoneId')}))
            boot = str(UUID(int=j+1))
            current = {k:deepcopy(old[k]) for k in ('hostname','instance_id','kernel','cpu_models',
                       'affinity_cpu_count','memory_total_bytes','private_addresses','block_devices')}
            current.update(schema='packed-c3-native-boot-v1',role=role,read_only=True,boot_id=boot,
                           boot_started_unix=int(CHECK.stamp(WAKE).timestamp())+8,ntp='yes',
                           started_at=AT,at=AT,mounts=[])
            for i, path in enumerate(CHECK.ROOTS[role]):
                disk = next(d for d in current['block_devices']['blockdevices'] if path in (d['mountpoints'] or []))
                disk.update(uuid=str(UUID(int=100+j*10+i)), **{'maj:min':f'252:{16+i*16}'})
                # Native device names may change after boot: cross-check the actual mount, not the old path.
                disk['path'] = f'/dev/vd{chr(ord("j")+i)}'
                current['mounts'].append(dict(path=path, source=disk['path'], fstype='ext4', device=disk['maj:min'],
                    stat_device=disk['maj:min'], uuid=disk['uuid'], options=['rw','noatime'],
                    total_bytes=disk['size']-200, available_bytes=disk['size']//2, total_inodes=1000, available_inodes=900))
                category,size,performance = shape['data_disks'][i]
                self.disks.append(dict(DiskId='d-'+disk['serial'],InstanceId=old['instance_id'],Type='data',Status='In_use',
                    ZoneId=shape['ZoneId'],Category=category,Size=size,PerformanceLevel=performance,SerialNumber=disk['serial']))
            if role == 'client':
                current['slot'] = dict(CHECK.SLOT,io_stat_readable=True)
            elif role == 'metadata':
                settings = dict(old['redis']['settings'],dir='/srv/ax-metadata/redis',bind='127.0.0.1 '+ip)
                # A numeric PID may repeat after reboot. The boot and start tick together identify it.
                pid = int(old['redis']['process_id'])
                p = dict(boot_id=boot,pid=pid,start_ticks=600)
                current['redis'] = dict(old['redis'],systemd_pid=pid,info_pid=pid,process=p,process_after=deepcopy(p),
                    settings=settings,memory=dict(used_memory=20000,used_memory_rss=30000,maxmemory=int(settings['maxmemory'])),
                    persistence=dict(aof_enabled='1',loading='0',aof_last_write_status='ok',aof_rewrite_in_progress='0',aof_current_size='100'))
            else:
                p = dict(boot_id=boot,pid=7301,start_ticks=200)
                svc = dict(old['rustfs'], pid=p['pid'], process=p, process_after=deepcopy(p),
                           restarts=0, mounts=[], data_dirs={}, bind_dirs={}, bind_mounts=[])
                for i,row in enumerate(current['mounts']):
                    path,bind = row['path']+'/rustfs-data',f'/srv/ax-rustfs-data-{i}'
                    svc['mounts'].append(dict(Source=bind,Destination=f'/data/rustfs{i}',Type='bind',RW=True))
                    svc['data_dirs'][path] = dict(device=row['device'],inode=100+i)
                    svc['bind_dirs'][bind] = deepcopy(svc['data_dirs'][path])
                    svc['bind_mounts'].append(bind)
                current['rustfs'] = svc
            self.native[role] = current

    def write(self, name, value):
        path = self.root/name
        path.write_text(json.dumps(value))
        return ref(path)

    def request(self):
        return dict(schema='packed-c3-reboot-evidence-request-v1',cell_id=CHECK.CELL,wake_requested_at=WAKE,
                    previous_stopped=ref(STOP),
                    instances=dict(self.write('instances.json',dict(RequestId='new-instance-readback',
                        TotalCount=3,Instances=dict(Instance=self.instances))),at=AT),
                    disks=dict(self.write('disks.json',dict(RequestId='new-disk-readback',
                        TotalCount=6,Disks=dict(Disk=self.disks))),at=AT),
                    native={role:dict(self.write(role+'.json',doc),sent_at=AT,received_at=AT) for role,doc in self.native.items()})

    def test_real_historical_pins_and_fresh_native_inputs(self):
        result = CHECK.verify(self.request(), NOW)
        self.assertTrue(result['evidence_consistent'])
        self.assertFalse(result['admission'])
        self.assertFalse(result['formal_acceptance'])
        self.assertNotIn('passed',result)
        self.assertNotIn('reviewed',result)
        self.assertEqual(len(result['roles']['objects']['disks']),4)

    def test_old_or_duplicate_native_boot_rejected(self):
        self.native['client']['boot_id'] = self.old['client']['boot_id']
        with self.assertRaisesRegex(ValueError,'Old boot'):
            CHECK.verify(self.request(), NOW)

    def test_cold_clock_and_api_freshness_rejected(self):
        request = self.request()
        request['instances']['at'] = WAKE
        with self.assertRaisesRegex(ValueError,'Stale or unbound ECS'):
            CHECK.verify(request, NOW.replace(minute=15, second=1))

    def test_native_clock_outside_coordinator_call_bounds_rejected(self):
        self.native['client']['started_at'] = '2026-11-12T04:03:57+00:00'
        with self.assertRaisesRegex(ValueError,'coordinator clock bounds'):
            CHECK.verify(self.request(), NOW)

    def test_swapped_disk_rejected_even_if_all_four_are_attached(self):
        self.native['objects']['mounts'][0]['source'] = self.native['objects']['mounts'][1]['source']
        with self.assertRaisesRegex(ValueError,'attachment or native source'):
            CHECK.verify(self.request(), NOW)

    def test_redis_stale_pid_and_nonempty_client_slot_rejected(self):
        self.native['metadata']['redis']['info_pid'] += 1
        with self.assertRaisesRegex(ValueError,'Redis active process'):
            CHECK.verify(self.request(), NOW)
        self.native['client']['slot']['cgroup.procs'] = '707'
        with self.assertRaisesRegex(ValueError,'client slot'):
            CHECK.verify(self.request(), NOW)

    def test_changed_container_and_wrong_bind_inode_rejected(self):
        rustfs = self.native['objects']['rustfs']
        image = rustfs['image_id']
        rustfs['image_id'] = 'sha256:'+'a'*64
        with self.assertRaisesRegex(ValueError,'service build'):
            CHECK.verify(self.request(), NOW)
        rustfs['image_id'] = image
        rustfs['bind_dirs']['/srv/ax-rustfs-data-0']['inode'] += 1
        with self.assertRaisesRegex(ValueError,'container bind'):
            CHECK.verify(self.request(), NOW)

    def test_source_pin_changed_rejected(self):
        request = self.request()
        Path(request['native']['client']['path']).write_text('{}')
        with self.assertRaisesRegex(ValueError,'Input SHA-256 mismatch'):
            CHECK.verify(request, NOW)

    def test_public_ip_and_missing_disk_rejected(self):
        self.instances[0]['EipAddress']['IpAddress'] = '192.0.2.9'
        with self.assertRaisesRegex(ValueError,'public exposure'):
            CHECK.verify(self.request(), NOW)
        self.disks.pop()
        with self.assertRaisesRegex(ValueError,'Missing, duplicate or extra DiskId'):
            CHECK.verify(self.request(), NOW)


if __name__ == '__main__':
    unittest.main()
