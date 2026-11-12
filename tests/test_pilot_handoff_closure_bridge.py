from pathlib import Path
import json
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch

from bench import pilot_handoff_closure_bridge as bridge
from bench.runtime import handoff
from bench.runtime.pilot_pools import resource_guard
import test_pilot_ledger_guard as ledger


@unittest.skipUnless(sys.platform.startswith("linux"), "actual coordinator flock requires Linux")
class TestC4HandoffClosureBridge(unittest.TestCase):
    def setUp(self):
        ledger.TestPilotLedgerGuard.setUpClass()
        self.f = f = ledger.TestPilotLedgerGuard()
        f.setUp()
        self.addCleanup(f.doCleanups)
        self.root = f.root
        self.out = self.root / "attempt"
        self.cell = next(c for c in f.new["cells"] if c["arm_id"] == "juicefs-move")
        f.reserve(f.current, f.new, self.cell, "block")
        documents = ledger.draft.draft(ledger.EVIDENCE / "plan.json", ledger.EVIDENCE / "inventory.json", "code-12")
        self.fleet = ledger.draft.setup_controller.actual_fleet(documents["inventory.json"], "code-12")
        self.inventory = self.pin("fleet", self.fleet)
        self.plan = self.pin("current-plan", f.new)
        self.anchor = dict(filesystem="juicefs", namespace=None, workspace=None, storage_identity_sha256="a" * 64)
        self.point = "/srv/ax-pilot-session"
        self.mount = dict(mount_id=72, point=self.point, filesystem="fuse.juicefs", source="vol", options="rw")
        self.process = dict(pid=41, boot_id="c05e3b63-e0e1-4eed-bcd1-bcf91e6e1500", start_ticks=62, state="S")
        self.started = dict(type="result", passed=True, mount=self.mount, process=self.process, anchor=self.anchor)
        self.s = dict(schema=handoff.V2, reviewed=True, filesystem="juicefs", mode="move", session_id="ses-original")
        self.readbacks = {}
        for side, node in zip(("source", "target"), (n for n in self.fleet["nodes"] if n["role"] == "executor")):
            peer = dict(transport="ssh", alias=node["ssh_alias"], python=node["python"], repo=node["repo"],
                        cgroup=node["slots"][0]["cgroup"], pins=[dict(path="/usr/bin/python3.12", sha256="1" * 64)])
            self.readbacks[side] = self.pin(side + "-mount", dict(schema="experiment-mount-v1", reviewed=True,
                anchor=self.anchor, filesystem="juicefs", cluster=self.cell["pool"], mount_point=self.point,
                suite_binding=dict(cell_id=self.cell["id"], plan=self.remote(self.plan, "/srv/ax/current-plan.json"))))
            self.s[side] = dict(node_id=node["id"], peer=peer, anchor=self.anchor,
                mount_directory=f"/srv/ax/c4/{side}-run",
                mount_spec=self.remote(self.readbacks[side], f"/srv/ax/c4/{side}-mount.json"))
        self.target = self.s["target"]
        self.route = dict(asset=dict(path="/srv/ax/c4/asset.json", sha256="2" * 64),
                          code=bridge.closure_code(self.target["peer"]), output="/srv/ax/c4/closure")
        self.run = dict(schema=bridge.SCHEMA, reviewed=True, cell_id=self.cell["id"],
            canonical_control=str(f.canonical), current_control=str(f.current),
            original_plan=self.pin("original-plan", f.old), current_plan=self.plan,
            mount_readbacks=self.readbacks, handoff=self.pin("handoff", self.s), target_close=self.route)
        self.calls = []
        self.bad_process = self.bad_archive = self.bad_close = self.other_archive = False
        self.addCleanup(patch.stopall)
        patch.object(bridge.handoff, "validate", return_value=[{"inventory": self.inventory}]).start()
        patch.object(bridge.handoff, "run", side_effect=self.measured).start()
        patch.object(bridge.transport, "ssh_command", side_effect=self.command).start()
        patch.object(bridge.subprocess, "run", side_effect=self.ssh).start()

    def pin(self, name, value):
        path = self.root / (name + ".json")
        bridge.write_new(path, value)
        return bridge.ref(path)

    @staticmethod
    def remote(local, path):
        return dict(path=path, sha256=local["sha256"])

    def locked(self, stage):
        self.calls.append(stage)
        with self.assertRaisesRegex(ValueError, "owns physical resource"):
            with resource_guard(self.f.canonical, self.f.b_cell, self.f.old):
                self.fail("original B pool acquired the target's real host/disk during " + stage)

    def measured(self, handoff_ref, out):
        self.locked("handoff")
        self.assertEqual(handoff_ref, self.run["handoff"])
        (out / "target-start").mkdir(parents=True)
        (out / "target-start" / "protocol.jsonl").write_text(json.dumps(self.started) + "\n", encoding="utf-8")
        bridge.write_new(out / "spec.json", self.s)
        cold = out / (handoff.COLD + ".json")
        bridge.write_new(cold, dict(passed=True, before_first_target_export=True, proof=self.started))
        result = dict(schema="experiment-handoff-result-v2", passed=True, target_mount_retained=True,
                      session_id=self.s["session_id"], mode=self.s["mode"], target_cold_check=bridge.ref(cold))
        bridge.write_new(out / "result.json", result)
        return result

    def authorize(self, context):
        self.locked("operator")
        self.assertEqual(context["original"]["live"], handoff.target_identity(self.started))
        m = self.target["mount_spec"]
        root = self.target["mount_directory"]
        ready = self.pin("actual-ready", dict(**self.started, spec=m))
        proc = self.pin("actual-process", dict(spec=m, process=dict(self.process, pid=42 if self.bad_process else 41),
                                               supervisor=dict(self.process, pid=40)))
        pilot = next(pin for pin in self.route["code"] if pin["path"].endswith("/pilot-node.py"))
        self.approved = dict(schema=bridge.CLOSE_REQUEST, reviewed=True, cell_id=self.cell["id"], mount_run=root,
                             mount_spec=m, ready=self.remote(ready, root + "/ready.json"),
                             process=self.remote(proc, root + "/process.json"), asset=self.route["asset"], pilot_node=pilot)
        local = self.pin("operator-reviewed-closure", self.approved)
        self.remote_request = self.remote(local, "/srv/ax/c4/operator-reviewed-closure.json")
        return dict(reviewed_request=local, remote_request=self.remote_request,
                    ready_readback=ready, process_readback=proc)

    def command(self, peer, argv):
        self.assertEqual(peer, self.target["peer"])
        self.assertEqual(argv, [peer["python"], "-I", "-B", "-u", "-c", bridge.BOOTSTRAP])
        return ["offline-ssh"]

    def ssh(self, cmd, *, input, stdout, stderr, check):
        self.locked("target-close")
        envelope = json.loads(input)
        self.assertEqual(envelope["cgroup"], self.target["peer"]["cgroup"])
        self.assertEqual(envelope["request"], self.remote_request)
        self.assertIn(self.approved["ready"], envelope["pins"])
        self.assertIn(self.approved["process"], envelope["pins"])
        def pin(path): return dict(path=path, sha256="5" * 64)
        root, out = self.target["mount_directory"], self.route["output"]
        result = dict(schema=bridge.CLOSE_RESULT, passed=True, cell_id=self.cell["id"], request=self.remote_request,
                      **{k:self.approved[k] for k in ("ready", "process", "mount_spec")}, anchor=self.anchor,
                      original_mount={**self.mount, "mount_id": 999} if self.bad_close else self.mount,
                      all_exited=True, all_unmounted=True, pending_upload_bytes=0, pending_upload_operations=0,
                      stopping=pin(root + "/stopping.json"), stopped=pin(root + "/stopped.json"),
                      closure=pin(out + "/suite-closure.json"), debt=dict(batch=pin(out + "/debt/result.json"),
                      samples=[dict(sample=pin(f"{out}/debt/sample-{i}.json"),
                                    native=pin(f"{out}/debt/native-{i}.prom")) for i in range(2)]))
        stdout.write(json.dumps(result).encode())

    def archive(self, context):
        self.locked("archive")
        self.assertEqual(context["target_closure"]["original_mount"], self.mount)
        self.assertEqual(bridge.load(bridge.pinned(context["target_closure_readback"])), context["target_closure"])
        if self.bad_archive:
            raise ValueError("offline archive rejected")
        return self.pin("archive-readback", dict(schema=bridge.ARCHIVE_READBACK, passed=True,
            cell_id="another-cell" if self.other_archive else self.cell["id"],
            handoff=context["original"]["result"], target_closure_readback=context["target_closure_readback"]))

    def start(self, req=None):
        if req is None:
            req = self.pin("guarded-run", self.run)
        return bridge.run_guarded(req, self.out, ownership=("owner", "token"),
                                  authorize_close=self.authorize, archive=self.archive)

    def test_original_host_lock_spans_handoff_target_stop_and_archive(self):
        result = self.start()
        self.assertEqual(self.calls, ["handoff", "operator", "target-close", "archive"])
        self.assertEqual(bridge.load(self.out / "result.json"), result)
        self.assertFalse(result["suite_ledger_advanced"])
        with resource_guard(self.f.canonical, self.f.b_cell, self.f.old):
            pass

    def test_unrelated_ready_process_cannot_issue_a_stop(self):
        self.bad_process = True
        with self.assertRaisesRegex(ValueError, "original target-start process"):
            self.start()
        self.assertEqual(self.calls, ["handoff", "operator"])
        self.assertEqual(bridge.load(self.out / "failed.json")["stage"], "operator-review")
        self.assertFalse((self.out / "target-close").exists())

    def test_wrong_target_closure_does_not_archive(self):
        self.bad_close = True
        with self.assertRaisesRegex(ValueError, "differs from original target"):
            self.start()
        self.assertEqual(self.calls, ["handoff", "operator", "target-close"])
        self.assertTrue((self.out / "target-close" / "stdout.json").is_file())
        self.assertEqual(bridge.load(self.out / "failed.json")["stage"], "target-close")

    def test_unrelated_archive_pin_does_not_mark_success(self):
        self.other_archive = True
        with self.assertRaisesRegex(ValueError, "does not bind this handoff and target closure"):
            self.start()
        self.assertFalse((self.out / "result.json").exists())
        self.assertEqual(bridge.load(self.out / "failed.json")["stage"], "archive")

    def test_failed_archive_preserves_claim_and_is_not_retried(self):
        self.bad_archive = True
        req = self.pin("guarded-run", self.run)
        with self.assertRaisesRegex(ValueError, "offline archive rejected"):
            self.start(req)
        self.assertEqual(bridge.load(self.out / "failed.json")["stage"], "archive")
        self.assertFalse((self.out / "result.json").exists())
        with sqlite3.connect(self.f.current / "state.sqlite") as db:
            self.assertEqual(db.execute("SELECT status FROM cells WHERE id=?", (self.cell["id"],)).fetchone(), ("claimed",))
        with self.assertRaisesRegex(ValueError, "new absolute output directory"):
            self.start(req)
        self.assertEqual(self.calls, ["handoff", "operator", "target-close", "archive"])

    def test_remote_bootstrap_executes_only_hash_verified_target_helper(self):
        root = self.root / "target-checkout"
        helper = root / bridge.HELPER
        helper.parent.mkdir(parents=True)
        helper.write_text("import json, sys\nprint(json.dumps(sys.argv[1:]))\n", encoding="utf-8")
        runtime = root / "bench/runtime/one.py"
        runtime.parent.mkdir(parents=True)
        runtime.write_text("# pinned module\n", encoding="utf-8")
        req = self.pin("remote-bootstrap-request", {"approved": True})
        out = str(self.root / "remote-out")
        exe = str(Path(sys.executable).resolve())
        envelope = dict(repo=str(root), cgroup=None, request=req, out=out,
                        pins=[bridge.ref(helper), bridge.ref(runtime), bridge.ref(exe), req])
        cmd = [exe, "-I", "-B", "-u", "-c", bridge.BOOTSTRAP]
        def invoke():
            # Popen is deliberately unmocked; the helper is a harmless temp file.
            with subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True) as child:
                stdout, stderr = child.communicate(json.dumps(envelope), timeout=10)
                return child.returncode, stdout, stderr
        code, stdout, stderr = invoke()
        self.assertEqual(code, 0, stderr)
        self.assertEqual(json.loads(stdout), ["--request", req["path"], "--sha256", req["sha256"], "--out", out])
        helper.write_text("raise AssertionError('changed helper was executed')\n", encoding="utf-8")
        code, stdout, stderr = invoke()
        self.assertNotEqual(code, 0)
        self.assertEqual(stdout, "")
        self.assertIn("target closure pin changed", stderr)
        self.assertNotIn("changed helper was executed", stderr)

    def test_wrong_plan_binding_refuses_before_handoff(self):
        self.run["current_plan"] = self.run["original_plan"]
        with self.assertRaisesRegex(ValueError, "unknown suite cell|current physical slot"):
            self.start()
        self.assertEqual(self.calls, [])
        self.assertFalse(self.out.exists())
