import ast
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]


def load_module(name, path, replacements=None):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    with patch.dict(sys.modules, replacements or {}):
        spec.loader.exec_module(module)
    return module


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def source_function(path, function_name, class_name=None):
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    owner = tree
    if class_name:
        owner = next(node for node in tree.body
                     if isinstance(node, ast.ClassDef) and node.name == class_name)
    return next(node for node in owner.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == function_name)


def ast_name(node, expected):
    return isinstance(node, ast.Name) and node.id == expected


def load_ast_functions(path, names, namespace):
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    selected = [node for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name in names]
    found = {node.name for node in selected}
    if found != set(names):
        raise AssertionError("Missing requested AST functions")
    module = ast.Module(body=selected, type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)
    return namespace


def call_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def fake_cluster_lib(root, input_dir):
    module = types.ModuleType("cluster_lib")
    module.RUN_ROOT = Path(root)
    module.INPUT_DIR = Path(input_dir)
    module.REPO_ROOT = Path("/test/repo")
    module.BIN_DIR = Path("/test/bin")
    module.RUN_NAMESPACE = "test-suite"
    module.ALL_HOST_NAMES = []
    module.HOST_BY_NAME = {
        name: {"ssh": "ssh-" + name}
        for name in ("A355", "A366", "A672", "A601", "A635", "A368")
    }
    module.utc_iso = lambda: "2026-01-01T00:00:00+00:00"
    module.collector_rpc = lambda *args, **kwargs: {}
    module.collector_mark_all = lambda *args, **kwargs: {"hosts": {}}
    module.collector_set_mounts = lambda *args, **kwargs: None
    module.summarize_phase_delta = lambda *args, **kwargs: {}
    module.endpoint_for_slot = lambda slot, index=0: f"http://endpoint-{index}:9000"
    module.sync_file_to_host = lambda *args, **kwargs: None
    module.run_on_host = lambda *args, **kwargs: types.SimpleNamespace(stdout=b'{"verified":true}')
    module.stop_mounts = lambda *args, **kwargs: []
    return module


def load_suite_groups(root, input_dir):
    cl = fake_cluster_lib(root, input_dir)
    layout = types.ModuleType("rustfs_layout_tools")
    layout.inspect_bucket_layout = lambda *args, **kwargs: {"summary": {}}
    layout.evict_bucket_page_cache = lambda *args, **kwargs: None

    support = types.ModuleType("c4_support")
    support.REDIS_URL = "redis://127.0.0.1:6379/0"
    support.placements = lambda count: [("A355", i + 1) for i in range(count)]
    support.command = lambda *args, **kwargs: types.SimpleNamespace(stdout=b'{"verified":true}')

    replay = types.ModuleType("run_c2")
    replay.CASES = ()
    replay.ensure_c2_bases = lambda policy: {"policy": policy}
    replay.batch_stop_mounts = lambda *args, **kwargs: []
    replay.execute_c2_cohort_arm = lambda *args, **kwargs: {
        "timing": {"app_done_seconds": 1.0},
        "metrics": {"actual_read_bytes": 10, "actual_write_bytes": 2, "trace_operations": 4},
    }

    readers = types.ModuleType("run_c3")
    readers.batch_c3_loader = lambda *args, **kwargs: []

    volumes = types.ModuleType("suite_volumes")
    volumes.prepare_dataset = lambda *args, **kwargs: {}
    volumes.mount = lambda *args, **kwargs: []
    volumes.init_packed_reader = lambda *args, **kwargs: {}
    volumes.admin = lambda *args, **kwargs: {}

    run_suite = types.ModuleType("run_suite")
    run_suite.save = write_json
    run_suite.select_backend = lambda enabled, out: {}
    run_suite.SYSTEMS = {
        "brewfs-native": "brewfs-native-overlay",
        "juicefs-redis": "juicefs",
        "juicefs-overlay": "juicefs-overlay",
    }
    replacements = {
        "cluster_lib": cl,
        "c4_support": support,
        "run_c2": replay,
        "run_c3": readers,
        "rustfs_layout_tools": layout,
        "suite_volumes": volumes,
        "run_suite": run_suite,
    }
    module = load_module(
        "suite_groups_runtime_test_module",
        ROOT / "bench/cluster/runtime/suite_groups.py",
        replacements,
    )
    return module, cl, support, replay, readers, layout, volumes


class RustFSBackendTests(unittest.TestCase):
    def setUp(self):
        self.backends = json.loads(
            (ROOT / "configs/rustfs-backends.json").read_text(encoding="utf-8"))

    def test_select_backend_syncs_each_backend_and_metrics_port_to_collectors(self):
        source = ROOT / "bench/cluster/runtime/run_suite.py"
        for enabled, name in ((False, "official"), (True, "dynamic")):
            with self.subTest(backend=name), tempfile.TemporaryDirectory() as temporary:
                config = self.backends[name]
                calls = {"endpoints": [], "sync": []}
                cluster = types.SimpleNamespace(
                    RUSTFS_BACKENDS=self.backends,
                    RUSTFS_BACKEND="dynamic",
                    RUSTFS_ENDPOINTS=list(self.backends["dynamic"]["endpoints"]),
                )

                def select(selected):
                    cluster.RUSTFS_BACKEND = selected
                    cluster.RUSTFS_ENDPOINTS[:] = self.backends[selected]["endpoints"]

                class Client:
                    def list_buckets(self):
                        return {"Buckets": []}

                def make_client(endpoint):
                    calls["endpoints"].append(endpoint)
                    return Client()

                def sync(host, local_path, remote_path):
                    contents = json.loads(Path(local_path).read_text(encoding="utf-8"))
                    calls["sync"].append((host, remote_path, contents))

                cluster.select_rustfs_backend = select
                cluster.make_s3_client = make_client
                cluster.sync_file_to_host = sync
                layout = types.SimpleNamespace(
                    discover_rustfs_nodes=lambda: {"backend": cluster.RUSTFS_BACKEND})
                namespace = {"cl": cluster, "layout": layout, "save": write_json, "Path": Path}
                functions = load_ast_functions(source, {"select_backend"}, namespace)
                out = Path(temporary)
                nodes = functions["select_backend"](enabled, out)

                self.assertEqual(nodes, {"backend": name})
                self.assertEqual(cluster.RUSTFS_BACKEND, name)
                self.assertEqual(calls["endpoints"], config["endpoints"])
                self.assertEqual([row[0] for row in calls["sync"]], config["nodes"])
                self.assertEqual({row[1] for row in calls["sync"]},
                                 {"/srv/ax-cluster/direct/runtime/rustfs-backend.json"})
                self.assertTrue(all(row[2]["name"] == name
                                    and row[2]["metrics_port"] == config["metrics_port"]
                                    and row[2]["endpoints"] == config["endpoints"]
                                    for row in calls["sync"]))
                saved = json.loads((out / "collector-backend.json").read_text(encoding="utf-8"))
                self.assertEqual(saved["metrics_port"], config["metrics_port"])

    def test_default_s3_helpers_follow_selected_backend(self):
        source = ROOT / "bench/cluster/runtime/cluster_lib.py"
        clients = []

        class Config:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        class Paginator:
            def paginate(self, **kwargs):
                return []

        class Client:
            def __init__(self, endpoint):
                self.endpoint_url = endpoint
                self.deleted = False

            def head_bucket(self, **kwargs):
                return {}

            def create_bucket(self, **kwargs):
                return {}

            def get_paginator(self, name):
                return Paginator()

            def delete_bucket(self, **kwargs):
                self.deleted = True

        class Boto3:
            def client(self, service, **kwargs):
                client = Client(kwargs["endpoint_url"])
                clients.append(client)
                return client

        namespace = {
            "BotoConfig": Config,
            "boto3": Boto3(),
            "SECRETS": {"s3_access_key": "test-key", "s3_secret_key": "test-secret"},
            "RUSTFS_BACKENDS": self.backends,
            "RUSTFS_BACKEND": "dynamic",
            "RUSTFS_ENDPOINTS": list(self.backends["dynamic"]["endpoints"]),
            "RUN_PREFIX": "axd1",
        }
        functions = load_ast_functions(source, {
            "select_rustfs_backend", "make_s3_client", "ensure_bucket",
            "delete_bucket_and_objects",
        }, namespace)
        for name in ("make_s3_client", "ensure_bucket", "delete_bucket_and_objects"):
            self.assertIsNone(functions[name].__defaults__[-1])

        for backend in ("official", "dynamic"):
            endpoint = self.backends[backend]["endpoints"][0]
            functions["select_rustfs_backend"](backend)
            self.assertEqual(functions["make_s3_client"]().endpoint_url, endpoint)
            functions["ensure_bucket"]("axd1-test-bucket")
            self.assertEqual(clients[-1].endpoint_url, endpoint)
            functions["delete_bucket_and_objects"]("axd1-test-bucket")
            self.assertEqual(clients[-1].endpoint_url, endpoint)
            self.assertTrue(clients[-1].deleted)

    def test_official_layout_uses_configured_data_directory(self):
        source = ROOT / "bench/cluster/runtime/rustfs_layout_tools.py"
        cluster = types.SimpleNamespace(
            RUSTFS_BACKEND="official", RUSTFS_BACKENDS=self.backends)
        functions = load_ast_functions(source, {"discover_rustfs_nodes"}, {"cl": cluster})

        actual = functions["discover_rustfs_nodes"]()
        official = self.backends["official"]
        self.assertEqual(set(actual), set(official["nodes"]))
        for host, endpoint in zip(official["nodes"], official["endpoints"]):
            self.assertEqual(actual[host]["data_dir"], official["data_dir"])
            self.assertEqual(actual[host]["endpoint"], endpoint)
            self.assertEqual(actual[host]["version"], official["version"])


class SuiteRuntimeTests(unittest.TestCase):
    def test_dispatches_each_of_56_jobs_once_to_its_group(self):
        suite_spec = json.loads((ROOT / "configs/experiment-suite.json").read_text(encoding="utf-8"))
        suite_builder = load_module("suite_builder_runtime_test_module", ROOT / "bench/cluster/suite.py")
        plan = suite_builder.build(suite_spec)
        self.assertEqual(plan["total_runs"], 56)

        with tempfile.TemporaryDirectory() as temporary:
            run_root = Path(temporary)
            write_json(run_root / "plan.json", plan)
            cl = fake_cluster_lib(run_root, run_root / "inputs")
            cl.REPO_ROOT = run_root / "repo"
            source_record = {"commit": "test-revision", "working_tree_changes": False}
            write_json(cl.REPO_ROOT / "source-record.json", source_record)
            layout = types.ModuleType("rustfs_layout_tools")
            fio = types.ModuleType("run_c1")
            groups = types.ModuleType("suite_groups")
            seen = []

            def handler(group):
                def dispatch(job, out, small=False):
                    seen.append((group, job["id"], job["group"], small))
                    return {"dispatched": True}
                return dispatch

            replacements = {
                "cluster_lib": cl,
                "rustfs_layout_tools": layout,
                "run_c1": fio,
            }
            module = load_module(
                "run_suite_runtime_test_module",
                ROOT / "bench/cluster/runtime/run_suite.py",
                replacements,
            )
            module.run_io = handler("01-io")
            groups.run_fork = handler("02-fork")
            groups.run_packed = handler("03-packed")
            groups.run_replay = handler("04-agent-replay")
            with patch.dict(sys.modules, {"suite_groups": groups}), \
                    patch.object(sys, "argv", ["run_suite.py"]), \
                    contextlib.redirect_stdout(io.StringIO()):
                module.main()

            self.assertEqual(len(seen), 56)
            self.assertEqual(len({item[1] for item in seen}), 56)
            self.assertEqual(
                {group: sum(item[0] == group for item in seen) for group in (
                    "01-io", "02-fork", "03-packed", "04-agent-replay")},
                {"01-io": 20, "02-fork": 12, "03-packed": 11, "04-agent-replay": 13},
            )
            self.assertTrue(all(item[0] == item[2] for item in seen))
            self.assertTrue(all(not item[3] for item in seen))
            status = json.loads((run_root / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(status["completed_runs"], 56)
            for job in plan["jobs"]:
                saved = run_root / job["output"] / "attempt-01/source-record.json"
                self.assertEqual(json.loads(saved.read_text()), source_record)
            self.assertTrue(all(item["state"] == "passed" for item in status["jobs"].values()))
            earlier = '03-packed-06-r1'
            selected = '03-packed-07-r1'
            status['jobs'][earlier] = {'state': 'failed', 'error': 'retained error'}
            status['jobs'][selected] = {'state': 'planned'}
            write_json(run_root / 'status.json', status)
            seen.clear()
            with patch.dict(sys.modules, {'suite_groups': groups}), \
                    patch.object(sys, 'argv', ['run_suite.py', '--start-at', selected]), \
                    contextlib.redirect_stdout(io.StringIO()):
                module.main()
            self.assertEqual([item[1] for item in seen], [selected])
            status = json.loads((run_root / 'status.json').read_text())
            self.assertEqual(status['jobs'][earlier], {'state': 'failed', 'error': 'retained error'})
            self.assertEqual(status['completed_runs'], 55)

    def test_fio_input_and_measurement_mounts_use_the_child_workspace(self):
        source = ROOT / "bench/cluster/runtime/run_c1.py"
        fork_child = source_function(source, "fork_child", "ParentPool")

        def system_branch(system):
            return next(
                node for node in ast.walk(fork_child)
                if isinstance(node, ast.If)
                and isinstance(node.test, ast.Compare)
                and isinstance(node.test.left, ast.Name)
                and node.test.left.id == "sys_name"
                and len(node.test.ops) == 1
                and isinstance(node.test.ops[0], ast.Eq)
                and len(node.test.comparators) == 1
                and isinstance(node.test.comparators[0], ast.Constant)
                and node.test.comparators[0].value == system
            )

        def input_mount_calls(branch):
            return [
                node for node in ast.walk(branch)
                if isinstance(node, ast.Call) and call_name(node.func) == "make_req"
                and len(node.args) > 7
                and isinstance(node.args[7], ast.Constant)
                and node.args[7].value == "input"
            ]

        native_branch = system_branch("brewfs-native-overlay")
        flat_branch = system_branch("brewfs-flat")
        native_prep_calls = input_mount_calls(native_branch)
        flat_prep_calls = input_mount_calls(flat_branch)
        self.assertEqual(len(native_prep_calls), 1)
        self.assertTrue(ast_name(native_prep_calls[0].args[5], "child_ws"))
        self.assertEqual(len(flat_prep_calls), 1)
        self.assertIsInstance(flat_prep_calls[0].args[5], ast.Constant)
        self.assertEqual(flat_prep_calls[0].args[5].value, "flat")
        self.assertIsInstance(flat_prep_calls[0].args[6], ast.Constant)
        self.assertEqual(flat_prep_calls[0].args[6].value, "none")

        executor = source_function(source, "execute_fio_family_run")
        measured_calls = [
            node for node in ast.walk(executor)
            if isinstance(node, ast.Call) and call_name(node.func) == "make_req"
            and len(node.args) > 5
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value in ("measure", "verify")
        ]
        self.assertEqual({node.args[1].value for node in measured_calls}, {"measure", "verify"})
        self.assertTrue(all(ast_name(node.args[5], "child_ws") for node in measured_calls))

        def helper_call(branch):
            calls = [
                node for node in ast.walk(branch)
                if isinstance(node, ast.Call) and call_name(node.func) == "run_on_host"
            ]
            return next(
                node for node in calls
                if any(
                    isinstance(arg, ast.Constant) and arg.value == "c1-io-helper"
                    for arg in ast.walk(node)
                )
            )

        self.assertIn("mounted[0]['mount_directory']", ast.unparse(helper_call(native_branch)))
        self.assertIn("mounts[0]['mount_directory']", ast.unparse(helper_call(flat_branch)))
        parent_source = source_function(source, "ensure_io_parent", "ParentPool")
        self.assertIn(
            "child-writable-layer",
            [node.value for node in ast.walk(parent_source)
             if isinstance(node, ast.Constant) and isinstance(node.value, str)],
        )
    def test_packed_candidate_is_used_for_registration_and_mount_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cl = fake_cluster_lib(root, root / 'inputs')
            cl.ALL_HOST_NAMES = ['A366']
            cl.PD_ENDPOINT = 'http://127.0.0.1:2379'
            support = types.ModuleType('c4_support')
            support.REDIS_URL = 'redis://127.0.0.1:6379/0'
            commands, requests = [], []
            support.command = lambda argv, *args, **kwargs: commands.append(argv) or types.SimpleNamespace(
                stdout=b'{"workspace_id":"candidate-workspace"}')
            cl.extract_json_from_cli = json.loads
            cl.generate_mounts_json = lambda rows, path: requests.extend(rows)
            cl.start_mounts = lambda *args, **kwargs: []
            volumes = load_module('suite_volumes_candidate_test_module',
                ROOT / 'bench/cluster/runtime/suite_volumes.py',
                {'cluster_lib': cl, 'c4_support': support})
            volume = {'engine': 'brewfs-packed', 'namespace': 'packed', 'bucket': 'bucket',
                      'manifest_key': 'manifest', 'data_config': '/private/config.json'}
            with patch.dict('os.environ', {'AX_PACKED_BREWFS_BINARY': '/candidate/brewfs'}):
                registered = volumes.init_packed_reader(volume, 'candidate-ns', root / 'register')
                volumes.mount(registered, registered['base'], 'reader', root)
                self.assertEqual(commands[0][0], str(Path('/candidate/brewfs')))
                self.assertEqual(requests[0]['binary'], str(Path('/candidate/brewfs')))
                native = dict(volume, engine='brewfs-native-overlay')
                self.assertEqual(volumes.brewfs_binary(native), cl.BIN_DIR / 'brewfs')
            with patch.dict('os.environ', {}, clear=True):
                self.assertEqual(volumes.brewfs_binary(volume), cl.BIN_DIR / 'brewfs')

    def test_corpus_places_at_most_1000_files_in_each_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cl = fake_cluster_lib(root, root / "inputs")
            support = types.ModuleType("c4_support")
            volumes = load_module(
                "suite_volumes_corpus_test_module",
                ROOT / "bench/cluster/runtime/suite_volumes.py",
                {"cluster_lib": cl, "c4_support": support},
            )

            data = volumes.corpus(1001)
            directories = sorted((data / "small").iterdir())
            counts = [sum(1 for item in directory.iterdir() if item.is_file())
                      for directory in directories]
            self.assertEqual([directory.name for directory in directories], ["d0000", "d0001"])
            self.assertEqual(counts, [1000, 1])
            marker = json.loads((data.parent / "ready.json").read_text(encoding="utf-8"))
            self.assertEqual(marker["files_per_directory"], 1000)
            self.assertEqual(marker["files"], 1001)

    def test_packed_prepare_read_registry_is_shared_by_phase_and_separate_by_dynamic(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "run"
            groups, cl, support, replay, readers, layout, volumes = load_suite_groups(
                root, Path(temporary) / "inputs")
            dynamic_calls = []
            groups.select_backend = lambda enabled, out: dynamic_calls.append(enabled) or {}
            mounted_buckets = []
            volumes.prepare_dataset = lambda system, dynamic, files, out: {
                "system": system, "engine": "juicefs",
                "bucket": f"bucket-{system}-{dynamic}", "base": "/",
                "namespace": "ns-" + dynamic, "meta_backend": "redis",
            }
            layout.inspect_bucket_layout = lambda *args, **kwargs: {"summary": {"objects": 1}}

            def mount(volume, workspace, identifier, out, host="A366", slot=1, overrides=None):
                mounted_buckets.append(volume["bucket"])
                return [{
                    "id": identifier, "host_name": host, "slot": slot, "engine": "juicefs",
                    "cgroup": "test-cgroup", "metrics_port": 19000 + slot,
                    "mount_directory": str(Path(out) / identifier),
                }]

            volumes.mount = mount
            support.command = lambda *args, **kwargs: types.SimpleNamespace(stdout=b'{"verified":true}')
            support.placements = lambda count: [("A355", index + 1) for index in range(count)]
            cl.stop_mounts = lambda *args, **kwargs: []
            cl.collector_mark_all = lambda *args, **kwargs: {"hosts": {}}
            cl.collector_set_mounts = lambda *args, **kwargs: None
            cl.summarize_phase_delta = lambda *args, **kwargs: {}
            cl.run_on_host = lambda *args, **kwargs: types.SimpleNamespace(stdout=b'{"verified":true}')
            readers.batch_c3_loader = lambda tasks, job_id: [{"task_count": len(tasks)}]
            replay.batch_stop_mounts = lambda *args, **kwargs: []

            registry_paths = {}
            read_results = {}
            for dynamic in ("on", "off"):
                prepare_job = {
                    "id": f"packed-{dynamic}-prepare", "seed": 10, "configuration": 1,
                    "parameters": {"phase": "prepare", "system": "juicefs-redis",
                                   "dynamic": dynamic, "files": 1001,
                                   "readers": 2, "epochs": 1},
                }
                prepare_out = root / "jobs" / (dynamic + "-prepare")
                groups.run_packed(prepare_job, prepare_out)
                registry = root / "prepared" / f"juicefs-redis-{dynamic}-1001.json"
                self.assertTrue(registry.is_file())
                registry_paths[dynamic] = registry
                self.assertEqual(json.loads(registry.read_text(encoding="utf-8"))["bucket"],
                                 f"bucket-juicefs-redis-{dynamic}")

                read_job = {
                    "id": f"packed-{dynamic}-read", "seed": 11, "configuration": 1,
                    "parameters": {"phase": "read", "system": "juicefs-redis",
                                   "dynamic": dynamic, "files": 1001,
                                   "readers": 2, "epochs": 1},
                }
                read_out = root / "jobs" / (dynamic + "-read")
                read_results[dynamic] = groups.run_packed(read_job, read_out)
                self.assertEqual(read_results[dynamic]["volume"]["bucket"],
                                 f"bucket-juicefs-redis-{dynamic}")

            self.assertNotEqual(registry_paths["on"], registry_paths["off"])
            self.assertEqual(dynamic_calls, [True, True, False, False])
            self.assertEqual(mounted_buckets.count("bucket-juicefs-redis-on"), 3)
            self.assertEqual(mounted_buckets.count("bucket-juicefs-redis-off"), 3)

    def test_reader_failure_saves_final_metrics_before_stopping_mounts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'run'
            groups, cl, support, replay, readers, layout, volumes = load_suite_groups(root, root / 'input')
            write_json(root / 'prepared/juicefs-redis-on-100000.json', {
                'engine': 'juicefs', 'base': '/', 'bucket': 'bucket'})
            volumes.mount = lambda volume, workspace, identifier, out, host, slot, overrides: [{
                'id': identifier, 'host_name': host, 'slot': slot, 'engine': 'juicefs',
                'cgroup': 'group', 'metrics_port': slot, 'mount_directory': '/mount'}]
            events = []
            def mark(job, stage):
                events.append(stage)
                return {'stage': stage}
            def load(*args):
                events.append('load')
                raise RuntimeError('reader I/O error')
            def stop(*args, **kwargs):
                events.append('stop')
                return [{'stopped': True}]
            cl.collector_mark_all = mark
            readers.batch_c3_loader = load
            replay.batch_stop_mounts = stop
            job = {'id': 'packed-read', 'seed': 10, 'configuration': 9, 'parameters': {
                'phase': 'read', 'system': 'juicefs-redis', 'dynamic': 'on',
                'files': 100000, 'readers': 2, 'epochs': 2}}
            out = root / 'job/attempt-01'
            with self.assertRaisesRegex(RuntimeError, 'reader I/O error'):
                groups.run_packed(job, out)
            self.assertEqual(events, ['read-start', 'load', 'read-end', 'stop'])
            self.assertEqual(json.loads((out / 'read-end.json').read_text()), {'stage': 'read-end'})
            self.assertEqual(json.loads((out / 'reader-stops.json').read_text()), [{'stopped': True}])
            events.clear()
            def fail_set_mounts(*args):
                events.append('set-mounts')
                raise RuntimeError('collector timeout')
            cl.collector_set_mounts = fail_set_mounts
            with self.assertRaisesRegex(RuntimeError, 'collector timeout'):
                groups.run_packed(job, root / 'job/attempt-02')
            self.assertEqual(events, ['set-mounts', 'stop'])

    def test_packed_reuses_uploaded_volume_and_verifies_before_registering(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'run'
            groups, cl, support, replay, readers, layout, volumes = load_suite_groups(root, root / 'input')
            job = {'id': 'packed-prepare', 'seed': 10, 'parameters': {
                'phase': 'prepare', 'system': 'brewfs-packed', 'dynamic': 'on', 'files': 100000}}
            source = root / 'job' / 'attempt-01' / 'volume.json'
            volume = {'system': 'brewfs-packed', 'dynamic': 'on', 'files': 100000,
                      'base': 'workspace', 'bucket': 'prepared-bucket', 'prepare_seconds': 319.0}
            write_json(source, volume)
            volumes.prepare_dataset = lambda *args: self.fail('Uploaded data must be reused')
            calls = []
            volumes.mount = lambda *args, **kwargs: calls.append(args[2]) or [{'mount_directory': '/mount'}]
            out = root / 'job' / 'attempt-02'
            result = groups.run_packed(job, out, prepared_path=source)
            self.assertEqual(result['prepare_seconds'], 319.0)
            self.assertEqual(result['prepared_source'], str(source))
            self.assertEqual(result['verification'], {'verified': True})
            self.assertEqual(calls, ['packed-prepare-attempt-02-verify'])
            registry = root / 'prepared/brewfs-packed-on-100000.json'
            self.assertTrue(registry.exists())
            registry.unlink()
            def fail_verify(*args, **kwargs):
                raise RuntimeError('read failed')
            support.command = fail_verify
            with self.assertRaisesRegex(RuntimeError, 'read failed'):
                groups.run_packed(job, out, prepared_path=source)
            self.assertFalse(registry.exists())
            volume['dynamic'] = 'off'
            write_json(source, volume)
            with self.assertRaisesRegex(ValueError, 'dynamic'):
                groups.run_packed(job, out, prepared_path=source)

    def test_native_readers_are_created_before_mounting(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'run'
            groups, cl, support, replay, readers, layout, volumes = load_suite_groups(root, root / 'input')
            write_json(root / 'prepared/brewfs-native-tikv-on-100000.json', {
                'engine': 'brewfs-native-overlay', 'base': 'parent', 'bucket': 'bucket', 'revision': 'revision'})
            events = []
            def create(volume, arguments, out):
                events.append(('create', arguments))
                return {'workspaces': [f'child-{i}' for i in range(32)]}
            def mount(volume, workspace, identifier, out, host, slot, overrides):
                events.append(('mount', workspace))
                return [{'id': identifier, 'host_name': host, 'slot': slot, 'engine': volume['engine'],
                         'cgroup': 'group', 'metrics_port': slot, 'mount_directory': '/mount/' + workspace}]
            volumes.admin = create
            volumes.mount = mount
            job = {'id': 'native-read', 'seed': 10, 'configuration': 6, 'parameters': {
                'phase': 'read', 'system': 'brewfs-native-tikv', 'dynamic': 'on',
                'files': 100000, 'readers': 32, 'epochs': 2}}
            groups.run_packed(job, root / 'job/attempt-02')
            self.assertEqual(events[0], ('create', ['fork-shared-bucket', '--revision', 'revision', '--count', '32']))
            self.assertEqual(events[1:], [('mount', f'child-{i}') for i in range(32)])

    def test_replay_task_counts_serialization_and_schedule_bounds(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "run"
            input_dir = Path(temporary) / "inputs"
            groups, cl, support, replay, readers, layout, volumes = load_suite_groups(root, input_dir)
            cases = tuple(f"case-{index}" for index in range(8))
            replay.CASES = cases
            boundaries = [
                {"boundary_id": f"b{index}", "operations": 10, "bytes": 4096}
                for index in range(6)
            ]
            for case in cases:
                write_json(input_dir / "traces" / case / "boundaries.json", {
                    "reviewed": True, "boundaries": boundaries, "events_operations": 60,
                    "events_bytes": 24576, "evidence": {"source": "test"},
                })
            calls = []
            replay.ensure_c2_bases = lambda policy: {"policy": policy}

            def execute(condition, system, repeat, cohort, bases, out):
                calls.append((condition["id"], len(cohort["shared_tasks"])))
                return {
                    "timing": {"app_done_seconds": 1.0},
                    "metrics": {"actual_read_bytes": 10, "actual_write_bytes": 2,
                                "trace_operations": 4},
                }

            replay.execute_c2_cohort_arm = execute
            groups.select_backend = lambda enabled, out: {}

            one_agent = {
                "id": "replay-one", "seed": 123, "repeat": 1,
                "parameters": {"dynamic": "on", "system": "brewfs-native", "agents": 1},
            }
            one_result = groups.run_replay(one_agent, root / "one-agent")
            self.assertEqual(one_result["agents"], 1)
            self.assertEqual(one_result["completed_tasks"], 8)
            self.assertEqual([size for _, size in calls], [1] * 8)
            self.assertEqual([condition for condition, _ in calls],
                             [f"replay-one-one-agent-b{index}" for index in range(1, 9)])

            for task in json.loads((root / "one-agent" / "tasks-plan.json").read_text(encoding="utf-8")):
                pacing = json.loads(Path(task["pacing_path"]).read_text(encoding="utf-8"))
                delays = [row["delay_ms"] for row in pacing["boundaries"]]
                self.assertTrue(all(1000 <= delay <= 7000 for delay in delays))
                self.assertEqual(sum(delays), 4000 * len(delays))
                self.assertEqual(pacing["total_think_ms"], 4000 * len(delays))

            calls.clear()
            multi_agent = {
                "id": "replay-multi", "seed": 456, "repeat": 1,
                "parameters": {"dynamic": "on", "system": "brewfs-native", "agents": 5},
            }
            multi_result = groups.run_replay(multi_agent, root / "multi-agent")
            self.assertEqual(multi_result["completed_tasks"], 5)
            self.assertEqual(len(multi_result["batches"]), 1)
            self.assertEqual(calls, [("replay-multi-multi-agent-b1", 5)])
            tasks = json.loads((root / "multi-agent" / "tasks-plan.json").read_text(encoding="utf-8"))
            self.assertEqual(len(tasks), 5)
            for task in tasks:
                pacing = json.loads(Path(task["pacing_path"]).read_text(encoding="utf-8"))
                delays = [row["delay_ms"] for row in pacing["boundaries"]]
                self.assertTrue(all(1000 <= delay <= 7000 for delay in delays))
                self.assertEqual(sum(delays), 4000 * len(delays))
                self.assertEqual(pacing["total_think_ms"], 4000 * len(delays))

    def test_replay_placement_profile_counts_mapping_and_validation(self):
        profile_source = ROOT / "configs/replay-placement-rustfs.json"
        base_profile = json.loads(profile_source.read_text(encoding="utf-8"))
        cluster_config = json.loads((ROOT / "configs/cluster.json").read_text(encoding="utf-8"))
        active_by_host = {host["name"]: host["active"] for host in cluster_config["hosts"]}
        verify_hosts = base_profile["verify_hosts"]

        for agents in (16, 64):
            with self.subTest(agents=agents), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary) / "run"
                input_dir = Path(temporary) / "inputs"
                groups, cl, support, replay, readers, layout, volumes = load_suite_groups(root, input_dir)
                cl.HOST_BY_NAME = {
                    name: {"ssh": "ssh-" + name, "active": active_by_host.get(name, 64)}
                    for name in cl.HOST_BY_NAME
                }
                write_json(root / "replay-placement.json", base_profile)
                replay.CASES = tuple(f"case-{index}" for index in range(8))
                boundaries = [{"boundary_id": "b0", "operations": 10, "bytes": 4096}]
                for case in replay.CASES:
                    write_json(input_dir / "traces" / case / "boundaries.json", {
                        "reviewed": True, "boundaries": boundaries, "events_operations": 10,
                        "events_bytes": 4096, "evidence": {"source": "test"},
                    })
                support.placements = lambda count: self.fail(
                    "A configured replay profile must provide the task placements")
                seen_cohorts = []
                replay.execute_c2_cohort_arm = lambda condition, system, repeat, cohort, bases, out: (
                    seen_cohorts.append(cohort["shared_tasks"]) or {
                        "timing": {"app_done_seconds": 1.0},
                        "metrics": {"actual_read_bytes": 10, "actual_write_bytes": 2,
                                    "trace_operations": 4},
                    }
                )
                job = {"id": f"replay-profile-{agents}", "seed": 123, "repeat": 1,
                       "parameters": {"dynamic": "on", "system": "brewfs-native", "agents": agents}}

                result = groups.run_replay(job, root / "job")
                tasks = json.loads((root / "job" / "tasks-plan.json").read_text(encoding="utf-8"))
                expected_counts = base_profile["counts"][str(agents)]
                self.assertEqual(result["completed_tasks"], agents)
                self.assertEqual(len(tasks), agents)
                self.assertEqual(len(seen_cohorts), 1)
                self.assertEqual(len(seen_cohorts[0]), agents)
                for host, count in zip(base_profile["hosts"], expected_counts):
                    host_tasks = [task for task in tasks if task["host"] == host]
                    self.assertEqual(len(host_tasks), count)
                    self.assertEqual(sorted(task["slot"] for task in host_tasks),
                                     list(range(1, count + 1)))
                    self.assertTrue(all(task["ssh"] == "ssh-" + host for task in host_tasks))
                    self.assertTrue(all(task["verify_host"] == verify_hosts[host]
                                        for task in host_tasks))
                self.assertEqual(len({(task["host"], task["slot"]) for task in tasks}), agents)
                self.assertEqual(
                    {task["task_id"]: task["verify_host"] for task in seen_cohorts[0]},
                    {task["task_id"]: verify_hosts[task["host"]] for task in tasks},
                )
                self.assertEqual(json.loads((root / "job" / "replay-placement.json").read_text()),
                                 base_profile)

        invalid_profiles = {
            "capacity": dict(base_profile, counts={
                **base_profile["counts"], "64": [17, 23, 16, 8]}),
            "mapping": dict(base_profile, verify_hosts={
                "A672": "A368", "A368": "A368", "A601": "A635", "A635": "A635"}),
        }
        for label, profile in invalid_profiles.items():
            with self.subTest(invalid=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary) / "run"
                input_dir = Path(temporary) / "inputs"
                groups, cl, support, replay, readers, layout, volumes = load_suite_groups(root, input_dir)
                cl.HOST_BY_NAME = {
                    name: {"ssh": "ssh-" + name, "active": active_by_host.get(name, 64)}
                    for name in cl.HOST_BY_NAME
                }
                write_json(root / "replay-placement.json", profile)
                replay.CASES = ("case-0",)
                write_json(input_dir / "traces" / "case-0" / "boundaries.json", {
                    "reviewed": True, "boundaries": [{"boundary_id": "b0", "operations": 10,
                                                        "bytes": 4096}],
                    "events_operations": 10, "events_bytes": 4096, "evidence": {"source": "test"},
                })
                job = {"id": "replay-profile-invalid", "seed": 123, "repeat": 1,
                       "parameters": {"dynamic": "on", "system": "brewfs-native", "agents": 64}}
                with self.assertRaises(ValueError):
                    groups.run_replay(job, root / "job")

    def test_replay_without_placement_profile_keeps_legacy_support_placements(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "run"
            input_dir = Path(temporary) / "inputs"
            groups, cl, support, replay, readers, layout, volumes = load_suite_groups(root, input_dir)
            replay.CASES = ("case-0", "case-1")
            write_json(input_dir / "traces" / "case-0" / "boundaries.json", {
                "reviewed": True, "boundaries": [{"boundary_id": "b0", "operations": 10,
                                                    "bytes": 4096}],
                "events_operations": 10, "events_bytes": 4096, "evidence": {"source": "test"},
            })
            write_json(input_dir / "traces" / "case-1" / "boundaries.json", {
                "reviewed": True, "boundaries": [{"boundary_id": "b0", "operations": 10,
                                                    "bytes": 4096}],
                "events_operations": 10, "events_bytes": 4096, "evidence": {"source": "test"},
            })
            calls = []
            support.placements = lambda count: calls.append(count) or [("A355", i + 1) for i in range(count)]
            job = {"id": "replay-legacy", "seed": 123, "repeat": 1,
                   "parameters": {"dynamic": "on", "system": "brewfs-native", "agents": 2}}

            result = groups.run_replay(job, root / "job")
            tasks = json.loads((root / "job" / "tasks-plan.json").read_text(encoding="utf-8"))
            self.assertEqual(result["completed_tasks"], 2)
            self.assertEqual(calls, [2])
            self.assertEqual([(task["host"], task["slot"]) for task in tasks],
                             [("A355", 1), ("A355", 2)])
            self.assertTrue(all("verify_host" not in task for task in tasks))


if __name__ == "__main__":
    unittest.main()
