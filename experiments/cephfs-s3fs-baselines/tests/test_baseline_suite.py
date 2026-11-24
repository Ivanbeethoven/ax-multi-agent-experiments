import ast
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest

BASELINE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BASELINE_DIR.parents[1]
sys.path.insert(0, str(BASELINE_DIR / "runtime"))
sys.path.insert(0, str(BASELINE_DIR / "scripts"))

import baseline_audit_mount as audit_mod
import baseline_cluster_lib as cl
import baseline_context as ctx
import baseline_groups as groups_mod
import baseline_host_batch as host_batch_mod
import baseline_mounts as mounts_mod
import baseline_suite as suite_mod
import sync_baselines


class BaselineSuiteTests(unittest.TestCase):
    def test_suite_validation_and_plan_export(self):
        suite = ctx.load_baseline_suite()
        summary = suite_mod.validate_suite(suite)
        self.assertTrue(summary["valid"])
        self.assertEqual(summary["total_runs"], 28)
        self.assertEqual(summary["per_system_runs"], {"cephfs": 14, "s3fs": 14})
        self.assertEqual(
            summary["groups"],
            {"01-io": 8, "02-fork": 8, "03-packed": 4, "04-agent-replay": 8},
        )

        full_plan = suite_mod.build_plan(suite)
        self.assertEqual(full_plan["total_jobs"], 28)
        self.assertEqual(len({j["id"] for j in full_plan["jobs"]}), 28)

        ceph_only = suite_mod.build_plan(suite, system_filter="cephfs")
        self.assertEqual(ceph_only["total_jobs"], 14)
        self.assertTrue(all(j["system"] == "cephfs" for j in ceph_only["jobs"]))

        s3fs_io = suite_mod.build_plan(suite, group_filter="01-io", system_filter="s3fs")
        self.assertEqual(s3fs_io["total_jobs"], 4)

    def test_alignment_with_main_experiment_suite(self):
        main_suite = json.loads((REPO_ROOT / "configs" / "experiment-suite.json").read_text(encoding="utf-8"))
        main_mounts = json.loads((REPO_ROOT / "configs" / "mount-profiles.json").read_text(encoding="utf-8"))
        trace_inputs = json.loads((REPO_ROOT / "configs" / "trace-inputs.json").read_text(encoding="utf-8"))
        cluster_cfg = json.loads((REPO_ROOT / "configs" / "cluster.json").read_text(encoding="utf-8"))
        base_suite = ctx.load_baseline_suite()
        base_mounts = mounts_mod.load_profiles()

        self.assertEqual(base_suite["repetitions"], main_suite["repetitions"])
        self.assertEqual(base_suite["seeds"], main_suite["seeds"])
        self.assertEqual(base_suite["resource_limits"], main_suite["resource_limits"])

        main_io_by_key = {
            (c["pattern"], c["operation"]): c
            for c in main_suite["groups"]["01-io"]["configurations"]
            if c["system"] == "juicefs-redis"
        }
        for cfg in base_suite["groups"]["01-io"]["configurations"]:
            ref = main_io_by_key[(cfg["pattern"], cfg["operation"])]
            for key in ("request_kib", "io_bytes", "jobs", "working_set_bytes", "fsync_bytes_all_jobs"):
                self.assertEqual(cfg[key], ref[key])

        main_fio_rp = main_mounts["resource_profiles"]["fio"]
        base_fio_rp = base_mounts["resource_profiles"]["fio"]
        self.assertEqual(base_fio_rp["memory_max_bytes"], main_fio_rp["memory_mib"] * 1024**2)
        self.assertEqual(base_fio_rp["disk_budget_bytes"], main_fio_rp["disk_mib"] * 1024**2)
        self.assertEqual(base_fio_rp["s3fs_max_dirty_data_mib"], main_fio_rp["write_staging_mib"])

        self.assertEqual(base_suite["groups"]["02-fork"]["files"], main_suite["groups"]["02-fork"]["files"])
        self.assertEqual(base_suite["groups"]["02-fork"]["file_bytes"], main_suite["groups"]["02-fork"]["file_bytes"])
        self.assertEqual(base_suite["groups"]["03-packed"]["files"], main_suite["groups"]["03-packed"]["files"])
        self.assertEqual(
            base_suite["groups"]["03-packed"]["files_per_directory"],
            main_suite["groups"]["03-packed"]["files_per_directory"],
        )
        self.assertEqual(base_suite["groups"]["03-packed"]["readers"], main_suite["groups"]["03-packed"]["readers"])
        self.assertEqual(base_suite["groups"]["03-packed"]["epochs"], main_suite["groups"]["03-packed"]["epochs"])
        for key in ("input_catalog", "wait_min_ms", "wait_max_ms", "wait_mean_ms", "tasks_per_agent"):
            self.assertEqual(
                base_suite["groups"]["04-agent-replay"][key],
                main_suite["groups"]["04-agent-replay"][key],
            )

        # Verify trace CASES order, host capacity, and cross-host bijection match mainline run_c2.py
        catalog_cases = {item["case"] for item in trace_inputs["cases"]}
        self.assertTrue(set(groups_mod.CASES).issubset(catalog_cases))
        run_c2_tree = ast.parse((REPO_ROOT / "bench" / "cluster" / "runtime" / "run_c2.py").read_text(encoding="utf-8"))
        main_consts = {
            node.targets[0].id: ast.literal_eval(node.value)
            for node in run_c2_tree.body
            if isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id in {"CASES", "VERIFY_HOST_MAP"}
        }
        self.assertEqual(groups_mod.CASES, main_consts["CASES"])
        self.assertEqual(groups_mod.VERIFY_HOST_MAP, main_consts["VERIFY_HOST_MAP"])
        self.assertEqual(
            [(h["name"], h["ssh"], h["slots"], h["active"]) for h in cl.HOSTS],
            [(h["name"], h["ssh"], h["slots"], h["active"]) for h in cluster_cfg["hosts"]],
        )
        for src_h, dst_h in groups_mod.VERIFY_HOST_MAP.items():
            self.assertNotEqual(src_h, dst_h)

    def test_cephfs_and_s3fs_mount_rendering_and_overrides(self):
        profiles = mounts_mod.load_profiles()

        ceph_req = {
            "id": "io-ceph-01",
            "phase": "measure",
            "engine": "cephfs",
            "host": "cluster-node-1",
            "slot": 1,
            "workspace": "/ax-baselines/run-baselines-001/io",
            "root": "/srv/ax-cluster/runs/baselines/run-baselines-001",
        }
        ceph_rec = mounts_mod.build_mount_record(ceph_req, profiles)
        ceph_inv = mounts_mod.render_mount_invocation(
            ceph_rec, {"subdir": "/ax-baselines/run-baselines-001/io"}, profiles
        )
        self.assertIn("--property=MemoryMax=4294967296", ceph_inv["systemd_argv"])
        self.assertIn("--property=MemorySwapMax=0", ceph_inv["systemd_argv"])
        self.assertIn("--property=MemoryAccounting=yes", ceph_inv["systemd_argv"])
        self.assertIn("--property=CPUAccounting=yes", ceph_inv["systemd_argv"])
        self.assertIn("--property=IOAccounting=yes", ceph_inv["systemd_argv"])
        self.assertIn("--property=TasksMax=16384", ceph_inv["systemd_argv"])
        self.assertIn("--property=LimitNOFILE=1048576", ceph_inv["systemd_argv"])
        self.assertTrue(all(not tok.startswith("-p=") for tok in ceph_inv["systemd_argv"]))
        self.assertEqual(
            ceph_rec["cgroup"],
            "/sys/fs/cgroup/ax.slice/ax-clients.slice/ax-baselines-cephfs-node-coord-1.service",
        )
        self.assertIn("--client_mountpoint=/ax-baselines/run-baselines-001/io", ceph_inv["exec_argv"])
        self.assertIn("--client_oc_size=536870912", ceph_inv["exec_argv"])
        self.assertTrue(ceph_rec["mount_directory"].startswith("/srv/ax-cluster/runs/baselines/"))

        # Buffered s3fs mount (used in 02-fork, 03-packed, 04-agent-replay)
        s3_req = {
            "id": "c2-s3fs-01",
            "phase": "measure",
            "engine": "s3fs",
            "host": "cluster-node-2",
            "slot": 2,
            "workspace": "runs/a001",
            "root": "/srv/ax-cluster/runs/baselines/run-baselines-001",
        }
        s3_rec = mounts_mod.build_mount_record(s3_req, profiles)
        s3_inv = mounts_mod.render_mount_invocation(
            s3_rec,
            {"bucket": "axbase1-c2-sklearn", "prefix": "runs/a001", "s3_endpoint": "http://175.168.6.72:9000"},
            profiles,
        )
        self.assertEqual(s3_inv["exec_argv"][2], "axbase1-c2-sklearn:/runs/a001")
        s3_opts = s3_inv["exec_argv"][5]
        self.assertIn("enable_unsigned_payload", s3_opts)
        self.assertIn("use_path_request_style", s3_opts)
        self.assertIn("use_cache=/srv/ax-cluster/runs/baselines/", s3_opts)
        self.assertNotIn("direct_io", s3_opts)
        self.assertNotIn("tmpdir=/tmp", s3_opts)

        # Direct-I/O s3fs + fio resource profile for 01-io fio (direct=1)
        s3_dio_req = dict(
            s3_req,
            id="io-s3fs-dio",
            resource_profile="fio",
            overrides={"direct_io": "on", "disk_cache": "off"},
        )
        s3_dio_rec = mounts_mod.build_mount_record(s3_dio_req, profiles)
        s3_dio_inv = mounts_mod.render_mount_invocation(
            s3_dio_rec,
            {"bucket": "axbase1-io", "prefix": "data", "s3_endpoint": "http://175.168.6.72:9000"},
            profiles,
        )
        self.assertIn("--property=MemoryMax=17179869184", s3_dio_inv["systemd_argv"])
        dio_opts = s3_dio_inv["exec_argv"][5]
        self.assertIn("direct_io", dio_opts)
        self.assertIn("max_dirty_data=8192", dio_opts)
        self.assertNotIn("use_cache=", dio_opts)

    def test_window_resource_and_64_agent_placement_budgets(self):
        profiles = mounts_mod.load_profiles()
        pts = cl.placements(64)
        self.assertEqual(len(pts), 64)

        for engine in ("cephfs", "s3fs"):
            reqs = []
            for idx, (host, slot) in enumerate(pts):
                v_host = groups_mod.VERIFY_HOST_MAP[host]
                reqs.append(
                    {
                        "id": f"{engine}-m-{idx:03d}",
                        "phase": "measure",
                        "engine": engine,
                        "host": cl.HOST_BY_NAME[host]["ssh"],
                        "slot": slot,
                        "workspace": f"ws-{idx}",
                        "windows": ["measure"],
                        "root": "/srv/ax-cluster/runs/baselines/run-baselines-001",
                    }
                )
                reqs.append(
                    {
                        "id": f"{engine}-v-{idx:03d}",
                        "phase": "verify",
                        "engine": engine,
                        "host": cl.HOST_BY_NAME[v_host]["ssh"],
                        "slot": slot,
                        "workspace": f"ws-{idx}",
                        "windows": ["verify"],
                        "root": "/srv/ax-cluster/runs/baselines/run-baselines-001",
                    }
                )
            doc = mounts_mod.build_mounts_document(reqs, profiles)
            self.assertEqual(len(doc["mounts"]), 128)

        # Duplicate slot in same window must fail
        bad_reqs = [
            {
                "id": "dup-1",
                "engine": "cephfs",
                "host": "cluster-node-1",
                "slot": 1,
                "workspace": "/a",
                "windows": ["measure"],
                "root": "/srv/ax-cluster/runs/baselines/run-baselines-001",
            },
            {
                "id": "dup-2",
                "engine": "cephfs",
                "host": "cluster-node-1",
                "slot": 1,
                "workspace": "/b",
                "windows": ["measure"],
                "root": "/srv/ax-cluster/runs/baselines/run-baselines-001",
            },
        ]
        with self.assertRaises(ValueError):
            mounts_mod.build_mounts_document(bad_reqs, profiles)

    def test_mount_auditor_and_secret_redaction(self):
        profiles = mounts_mod.load_profiles()
        rec = mounts_mod.build_mount_record(
            {
                "id": "aud-s3",
                "engine": "s3fs",
                "host": "cluster-node-1",
                "slot": 3,
                "workspace": "data",
                "root": "/srv/ax-cluster/runs/baselines/run-baselines-001",
            },
            profiles,
        )
        mp = rec["mount_directory"]
        fake_mountinfo = f"512 31 0:68 / {mp} rw,nosuid,nodev,noatime - fuse.s3fs s3fs rw,user_id=0,group_id=0\n"
        ok_res = audit_mod.verify_mount_state(
            record=rec,
            pid=12345,
            cmdline=["/usr/bin/s3fs", "-f", "axbase1:/data", mp, "-o", "enable_unsigned_payload,tmpdir=/srv/x"],
            environ_keys=["PATH", "LANG"],
            mountinfo_text=fake_mountinfo,
            memory_max_raw="4294967296\n",
            memory_swap_max_raw="0\n",
            secret_values=["TOPSECRET123"],
        )
        self.assertTrue(ok_res["ok"], ok_res["errors"])

        bad_res = audit_mod.verify_mount_state(
            record=rec,
            pid=12345,
            cmdline=["/usr/bin/s3fs", "-f", "TOPSECRET123", mp, "-o", "tmpdir=/tmp"],
            environ_keys=["AWS_SECRET_ACCESS_KEY"],
            mountinfo_text="",
            memory_max_raw="max\n",
            memory_swap_max_raw="1024\n",
            secret_values=["TOPSECRET123"],
        )
        self.assertFalse(bad_res["ok"])
        self.assertGreaterEqual(len(bad_res["errors"]), 4)
        self.assertIn("<redacted>", bad_res["redacted_cmdline"])

    def test_fio_jobfile_and_think_plan_generation(self):
        seqw_txt, is_read, vmode, seq_w = groups_mod.build_fio_jobfile(
            {
                "pattern": "sequential",
                "operation": "write",
                "request_kib": 1024,
                "io_bytes": 2147483648,
                "fsync_bytes_all_jobs": 67108864,
            },
            "/mnt/test",
            2026111501,
        )
        self.assertFalse(is_read)
        self.assertEqual(vmode, "seqwrite")
        self.assertEqual(seq_w, 268435456)
        self.assertIn("size=268435456\n", seqw_txt)
        self.assertIn("fsync=8\n", seqw_txt)
        self.assertIn("direct=1\n", seqw_txt)

        randw_txt, is_read2, vmode2, _ = groups_mod.build_fio_jobfile(
            {
                "pattern": "random",
                "operation": "write",
                "request_kib": 4,
                "io_bytes": 67108864,
                "fsync_bytes_all_jobs": 67108864,
            },
            "/mnt/test",
            2026111501,
        )
        self.assertFalse(is_read2)
        self.assertEqual(vmode2, "randwrite")
        self.assertIn("fsync=2048\n", randw_txt)

        fake_bounds = {
            "reviewed": True,
            "events_operations": 100,
            "events_bytes": 40960,
            "evidence": "unit-test",
            "boundaries": [{"event_index": i * 10} for i in range(6)],
        }
        plan1 = groups_mod.build_think_plan(fake_bounds, 16, "a001", 2026111501)
        plan2 = groups_mod.build_think_plan(fake_bounds, 16, "a001", 2026111501)
        self.assertEqual(plan1, plan2)
        self.assertEqual(plan1["total_think_ms"], 24000)
        self.assertTrue(all(1000 <= b["delay_ms"] <= 7000 for b in plan1["boundaries"]))

    def test_tiny_mode_selection_and_scaling(self):
        suite = ctx.load_baseline_suite()
        tiny_plan = suite_mod.build_plan(suite, tiny=True)
        self.assertEqual(tiny_plan["total_jobs"], 14)
        by_sys = {
            s: sum(1 for j in tiny_plan["jobs"] if j["system"] == s)
            for s in ("cephfs", "s3fs")
        }
        self.assertEqual(by_sys, {"cephfs": 7, "s3fs": 7})
        by_grp = {
            g: sum(1 for j in tiny_plan["jobs"] if j["group"] == g)
            for g in suite_mod.EXPECTED_GROUPS
        }
        self.assertEqual(by_grp, {"01-io": 4, "02-fork": 4, "03-packed": 4, "04-agent-replay": 2})

        # Verify tiny fio jobfile keeps io_size >= blocksize across 8 jobs
        tiny_seq_txt, is_r, _, _ = groups_mod.build_fio_jobfile(
            {"pattern": "sequential", "operation": "read", "request_kib": 1024, "io_bytes": 8 * 1024 * 1024},
            "/mnt/tiny",
            2026111501,
            bytes_per_job=2 * 1024 * 1024,
            jobs=8,
        )
        self.assertTrue(is_r)
        self.assertIn("size=2097152\n", tiny_seq_txt)
        self.assertIn("io_size=1048576\n", tiny_seq_txt)

        tiny_rand_txt, is_r2, vmode2, _ = groups_mod.build_fio_jobfile(
            {"pattern": "random", "operation": "write", "request_kib": 4, "io_bytes": 512 * 1024},
            "/mnt/tiny",
            2026111501,
            bytes_per_job=2 * 1024 * 1024,
            jobs=8,
        )
        self.assertFalse(is_r2)
        self.assertEqual(vmode2, "randwrite")
        self.assertIn("io_size=65536\n", tiny_rand_txt)

        # Verify tiny think-plan stays valid for full-fuse-replay Pacer::validate
        fake_bounds = {
            "reviewed": True,
            "events_operations": 80,
            "events_bytes": 16384,
            "evidence": "tiny-test",
            "boundaries": [{"event_index": i * 8, "before_seq": i * 8 + 2, "id": f"b{i}", "kind": "tool"} for i in range(6)],
        }
        tplan = groups_mod.build_think_plan(fake_bounds, 2, "a001", 2026111501, min_ms=50, max_ms=150, mean_ms=100)
        self.assertEqual(tplan["min_interval_ms"], 50)
        self.assertEqual(tplan["max_interval_ms"], 150)
        self.assertEqual(tplan["total_think_ms"], 600)
        self.assertTrue(all(50 <= b["delay_ms"] <= 150 for b in tplan["boundaries"]))

    def test_full_fuse_copy_mechanism_and_cost_schema(self):
        suite = ctx.load_baseline_suite()
        for cfg in suite["groups"]["02-fork"]["configurations"]:
            if cfg["mode"] == "fork":
                self.assertEqual(cfg["fork_mechanism"], "full-fuse-data-copy")

        empty_cost = cl.fuse_copy_workspaces("s3fs", [], Path("/tmp"))
        self.assertFalse(empty_cost["native_fork_supported"])
        for key in ("timing", "client_resources", "storage_cost", "backend_io_cost"):
            self.assertIn(key, empty_cost)

        # Ensure no server-side S3 copy_object shortcut exists anywhere in baseline runtime/scripts
        for py_path in BASELINE_DIR.rglob("*.py"):
            if py_path.resolve() == Path(__file__).resolve():
                continue
            tree = ast.parse(py_path.read_text(encoding="utf-8"), filename=str(py_path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute):
                    self.assertNotEqual(
                        node.attr,
                        "copy_object",
                        f"Server-side S3 copy_object shortcut is forbidden in {py_path}",
                    )

        # Verify _copy_tree_parallel copies nested directory contents byte-for-byte
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src"
            dst = Path(td) / "dst"
            (src / "nested" / "sub").mkdir(parents=True)
            (src / "a.bin").write_bytes(b"alpha" * 900)
            (src / "nested" / "sub" / "b.bin").write_bytes(b"bravo" * 1500)
            cp_stats = host_batch_mod._copy_tree_parallel(src, dst, file_workers=4)
            self.assertEqual(cp_stats["files"], 2)
            self.assertEqual(cp_stats["bytes"], 5 * 900 + 5 * 1500)
            self.assertEqual((dst / "a.bin").read_bytes(), (src / "a.bin").read_bytes())
            self.assertEqual((dst / "nested" / "sub" / "b.bin").read_bytes(), (src / "nested" / "sub" / "b.bin").read_bytes())

    def test_bundle_builder_includes_required_files(self):
        raw = sync_baselines.build_bundle_bytes()
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as tf:
            names = set(tf.getnames())
        for expected in (
            "configs/baseline-suite.json",
            "configs/baseline-mount-profiles.json",
            "configs/baseline-runtime.json",
            "repo-configs/cluster.json",
            "repo-configs/rustfs-backends.json",
            "scripts/run_baseline_suite.py",
            "scripts/baseline_groups.py",
            "scripts/baseline_mount_driver.py",
            "scripts/s3fs_hardlink_copy_shim.c",
            "scripts/setup_baselines.py",
            "bench/cluster/c4_workload.py",
        ):
            self.assertIn(expected, names)

    def test_strictly_zero_hashes_or_checksums(self):
        banned_modules = {"hashlib", "zlib", "binascii", "hmac"}
        banned_tokens = (
            "sha" + "256",
            "sha" + "512",
            "sha" + "1",
            "md" + "5",
            "crc" + "32",
            "xx" + "hash",
            "blake" + "2",
            "blake" + "3",
        )
        this_file = Path(__file__).resolve()
        checked_py = 0
        for py_path in BASELINE_DIR.rglob("*.py"):
            checked_py += 1
            src = py_path.read_text(encoding="utf-8")
            tree = ast.parse(src, filename=str(py_path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotIn(alias.name.split(".")[0], banned_modules, f"Banned import in {py_path}")
                elif isinstance(node, ast.ImportFrom) and node.module:
                    self.assertNotIn(node.module.split(".")[0], banned_modules, f"Banned import in {py_path}")
            if py_path.resolve() != this_file:
                low = src.lower()
                for tok in banned_tokens:
                    self.assertNotIn(tok, low, f"Banned token {tok!r} found in {py_path}")

        for extra_path in list(BASELINE_DIR.rglob("*.json")) + list(BASELINE_DIR.rglob("*.c")):
            low = extra_path.read_text(encoding="utf-8").lower()
            for tok in banned_tokens:
                self.assertNotIn(tok, low, f"Banned token {tok!r} found in {extra_path}")

        self.assertGreaterEqual(checked_py, 12)


if __name__ == "__main__":
    unittest.main()
