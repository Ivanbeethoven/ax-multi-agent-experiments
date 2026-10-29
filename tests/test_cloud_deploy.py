"""Offline checks of deployment rejection and S3 probe failure boundaries."""
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

DEPLOY = Path(__file__).resolve().parents[1] / "infra" / "alicloud" / "deploy"


def load(name):
    spec = importlib.util.spec_from_file_location(name, DEPLOY / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rustfs = load("generate_rustfs")
ceph = load("generate_ceph")
probe = load("s3_probe")


def rustfs_spec():
    return {
        "mode": "distributed", "image": "rustfs/rustfs@sha256:" + "a" * 64,
        "nodes": [{"private_ip": f"10.72.1.{20+i}", "data_mounts": [f"/mnt/rustfs{j}" for j in range(4)],
                   "log_dir": "/var/log/rustfs", "credentials_file": "/etc/agent-experiment/rustfs.env"} for i in range(4)],
    }


def ceph_spec():
    return {"nodes": [{"name": f"ceph-{i}", "private_ip": f"10.72.1.{30+i}",
                       "devices": [f"/dev/disk/by-id/virtio-d-test{i}"]} for i in range(3)]}


class GeneratorTests(unittest.TestCase):
    def test_formal_budget_and_dynamic_feature_controls(self):
        spec = rustfs_spec(); spec['mode']='single-node-four-disk'; spec['nodes']=spec['nodes'][:1]
        spec['experiment']={'project_name':'ax-test-c3-n1','cpus':3,'memory_bytes':16*1024**3,'stage':'D','layout_profile':'enabled'}
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp)/'generated'; rustfs.generate(spec,out)
            compose=json.loads((out/'rustfs1/compose.json').read_text()); service=compose['services']['rustfs']
            self.assertEqual(compose['name'],'ax-test-c3-n1'); self.assertEqual(service['mem_limit'],16*1024**3)
            self.assertEqual(service['cpus'],3); self.assertEqual(service['restart'],'no')
            self.assertEqual(service['environment']['RUSTFS_EC_READ_QUANTUM_FLEET_CONFIRMED'],'true')
        spec['experiment']['stage']='G'; self.reject(rustfs.generate,spec)
    def test_single_node_four_disks_and_placeholder_rejection(self):
        spec = rustfs_spec()
        spec['mode'] = 'single-node-four-disk'; spec['nodes'] = spec['nodes'][:1]
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / 'generated'
            rustfs.generate(spec, out)
            service = json.loads((out/'rustfs1/compose.json').read_text())['services']['rustfs']
            self.assertEqual(service['environment']['RUSTFS_VOLUMES'], '/data/rustfs{0...3}')
            self.assertEqual(len(service['volumes']), 5)
        spec['image'] = 'REGISTRY/rustfs@sha256:' + 'a' * 64
        self.reject(rustfs.generate, spec)

    def reject(self, generator, spec):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "generated"
            with self.assertRaises(ValueError):
                generator(spec, output)
            self.assertFalse(output.exists(), "invalid last node must not leave runnable first-node output")

    def test_rustfs_rejects_unpinned_image_and_repeated_ip(self):
        spec = rustfs_spec()
        spec["image"] = "rustfs/rustfs:latest"
        self.reject(rustfs.generate, spec)
        spec = rustfs_spec()
        spec["nodes"][-1]["private_ip"] = spec["nodes"][0]["private_ip"]
        self.reject(rustfs.generate, spec)

    def test_rustfs_rejects_duplicate_nested_root_and_traversal_mounts(self):
        for bad in ("/mnt/rustfs0", "/mnt/rustfs0/subdir", "/mnt", "/", "//", "/mnt/a/../rustfs1", r"C:\data\rustfs"):
            with self.subTest(path=bad):
                spec = rustfs_spec()
                spec["nodes"][-1]["data_mounts"][1] = bad
                self.reject(rustfs.generate, spec)

    def test_rustfs_keeps_credentials_outside_mounts_and_compose_environment(self):
        spec = rustfs_spec()
        spec["nodes"][-1]["credentials_file"] = "/mnt/rustfs0/credentials.env"
        self.reject(rustfs.generate, spec)
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "generated"
            rustfs.generate(rustfs_spec(), output)
            self.assertEqual(len(list(output.glob("rustfs*/compose.json"))), 4)
            service = json.loads((output / "rustfs1" / "compose.json").read_text())["services"]["rustfs"]
            self.assertNotIn("RUSTFS_SECRET_KEY", service["environment"])
            self.assertEqual(len(service["volumes"]), 5)

    def test_ceph_rejects_root_traversal_and_duplicate_devices(self):
        for bad in ("/", "/dev/disk/by-id", "/dev/disk/by-id/../sda", "/dev/disk/by-id/virtio-d-test0"):
            with self.subTest(path=bad):
                spec = ceph_spec()
                spec["nodes"][-1]["devices"] = [bad]
                self.reject(ceph.generate, spec)
        spec = ceph_spec()
        spec["nodes"][-1]["devices"] *= 2
        self.reject(ceph.generate, spec)

    def test_ceph_preview_uses_only_explicit_devices(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "generated"
            ceph.generate(ceph_spec(), output)
            preview = (output / "preview.sh").read_text()
            self.assertIn("--dry-run", preview)
            self.assertNotIn("all-available-devices", preview)


class FakeS3:
    def __init__(self, corrupt=False, cleanup_failure=False):
        self.corrupt, self.cleanup_failure = corrupt, cleanup_failure
        self.bodies, self.deleted = [], []

    def create_bucket(self, **kwargs):
        pass

    def put_object(self, **kwargs):
        self.payload = kwargs["Body"]

    def head_object(self, **kwargs):
        return {"ContentLength": len(self.payload)}

    def get_object(self, **kwargs):
        payload = self.payload[4096:8192] if "Range" in kwargs else self.payload
        body = io.BytesIO(b"corrupt" if self.corrupt else payload)
        self.bodies.append(body)
        return {"Body": body}

    def list_objects_v2(self, **kwargs):
        return {"Contents": [{"Key": "probe.bin"}]}

    def delete_object(self, **kwargs):
        self.deleted.append("object")

    def delete_bucket(self, **kwargs):
        if self.cleanup_failure:
            raise RuntimeError("cleanup failed")
        self.deleted.append("bucket")


class S3ProbeTests(unittest.TestCase):
    def test_corrupt_get_is_failure_and_body_is_closed(self):
        client = FakeS3(corrupt=True)
        with self.assertRaisesRegex(RuntimeError, "content differs"):
            probe.run_probe(client)
        self.assertTrue(all(body.closed for body in client.bodies))
        self.assertEqual(client.deleted, ["object", "bucket"])

    def test_success_requires_cleanup_and_closes_all_bodies(self):
        client = FakeS3()
        self.assertEqual(probe.run_probe(client)["cleanup"], "passed")
        self.assertEqual(client.deleted, ["object", "bucket"])
        self.assertEqual(len(client.bodies), 2)
        self.assertTrue(all(body.closed for body in client.bodies))
        with self.assertRaisesRegex(RuntimeError, "cleanup failed"):
            probe.run_probe(FakeS3(cleanup_failure=True))


if __name__ == "__main__":
    unittest.main()
