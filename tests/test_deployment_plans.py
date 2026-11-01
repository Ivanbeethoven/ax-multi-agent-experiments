"""Contracts for portable C1-C4 preparation; never connects to a server."""
from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bench"))
import scale_plan as scale
import experiment_inventory as inventory
import host_preflight as host


def plan(**kw):
    return scale.build(campaign="ecs-20261101", **kw)


def allocated(p):
    value = inventory.from_plan(p, "a" * 64)
    value["campaign_id"] = "test-portable"
    for i, node in enumerate(value["nodes"]):
        node.update(ssh_alias=node["id"], private_ip=f"10.2.{i // 250}.{i % 250 + 1}",
                    resource_id=f"asset-{i}", hardware_class=node["role"],
                    host_key_verified=True, owned_by_campaign=False,
                    work_root="/srv/experiment", min_free_bytes_per_disk=1024**3,
                    min_free_inodes_per_disk=10000,
                    data_mounts=[f"/mnt/data-{j}" for j in range(len(node["requirements"]["data_disks_gib"]))])
        if node["role"] in ("source", "target"): node["hardware_class"] = "matched-client"
    return value


class PortableTests(unittest.TestCase):
    def test_host_budget_rejects_old_small_shape_and_insufficient_volumes(self):
        node={'requirements':{'vcpus':32,'memory_gib':128,'data_disks_gib':[1024,1024]}}
        volumes=[{'total_bytes':1024**4,'free_bytes':1024**4//2} for _ in range(2)]
        host.check_role_resources(node,32,124*1024**3,volumes)
        for cpu,memory,disks in [(8,124*1024**3,volumes),(32,32*1024**3,volumes),
                                 (32,124*1024**3,volumes[:1]),
                                 (32,124*1024**3,[{'total_bytes':256*1024**3,'free_bytes':128*1024**3}]*2),
                                 (32,124*1024**3,[{'total_bytes':1024**4,'free_bytes':1024**4//8}]*2)]:
            with self.assertRaises(ValueError):host.check_role_resources(node,cpu,memory,disks)

    def test_documented_cli_produces_provider_inventory_without_actions(self):
        root=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            for provider in ('aliyun','provided'):
                for cluster in ('C1','C2','C3','C4'):
                    target=Path(temp)/f'{provider}-{cluster}.json'
                    inv=target.with_suffix('.inventory.json')
                    subprocess.run([sys.executable,str(root/'bench/scale_plan.py'),'--campaign','ecs-20261101',
                        '--provider',provider,'--isolation','docker','--cluster',cluster,'--output',str(target)],
                        check=True,capture_output=True,text=True)
                    subprocess.run([sys.executable,str(root/'bench/experiment_inventory.py'),'from-plan',
                        '--plan',str(target),'--output',str(inv)],check=True,capture_output=True,text=True)
                    value=json.loads(inv.read_text())
                    self.assertEqual(value['provider'],provider)
                    self.assertTrue(all(n['resource_id'] is None for n in value['nodes']))
                    with self.assertRaises(ValueError):inventory.validate(value)

    def test_explicit_c1_inputs_and_independent_writeback(self):
        main = plan(cluster="C1")
        self.assertEqual(main["group_counts"], {"trace-weak":90, "trace-coverage":24,
                         "trace-fixed-queue":18, "overlay-scale-ablation":54})
        pilot = plan(cluster="C1", profile="pilot")
        self.assertEqual(pilot["total_cells"], 6)
        self.assertEqual(pilot["parallel_reference_compute"], {"vcpus":168, "memory_gib":672})
        self.assertEqual({c["params"]["trace_case"] for c in pilot["cells"]}, {"pytest-full-02"})
        writeback = plan(cluster="C1", study="writeback")
        self.assertEqual(writeback["total_cells"], 186)
        self.assertFalse({c["id"] for c in main["cells"]} & {c["id"] for c in writeback["cells"]})
        self.assertEqual(plan(cluster="C1", profile="expanded")["total_cells"] - main["total_cells"], 24)
        self.assertFalse({c['id'] for c in main['cells']} & {c['id'] for c in plan(cluster='C1',profile='expanded')['cells']})

    def test_both_providers_match_experiments_but_have_distinct_ids(self):
        cloud, servers = plan(provider="aliyun"), plan(provider="provided")
        self.assertEqual(cloud["group_counts"], servers["group_counts"])
        self.assertFalse({c["id"] for c in cloud["cells"]} & {c["id"] for c in servers["cells"]})
        self.assertTrue(all(n["requirements"]["candidate_ecs_type"] for n in cloud["resource_requirements"]))
        self.assertTrue(all(n["requirements"]["candidate_ecs_type"] is None for n in servers["resource_requirements"]))
        for p in (cloud, servers):
            self.assertFalse(p["execution_enabled"])
            self.assertFalse(p["provisioning_enabled"])
            self.assertFalse(p["requires_kubernetes"])
            self.assertFalse(p["provider_contract"]["allow_host_deletion"])
            self.assertEqual(p["parallel_node_count"], 47)
            self.assertEqual(p["parallel_reference_compute"], {"vcpus":1536, "memory_gib":6144})
            pairs = defaultdict(list)
            for cell in p["cells"]: pairs[cell["paired_block"]].append(cell)
            for rows in pairs.values():
                self.assertEqual(len(rows), len({c["method"] for c in rows}))
                self.assertEqual(sorted(c["order_in_block"] for c in rows), list(range(1, len(rows)+1)))
                self.assertTrue(all(not c["execution_enabled"] for c in rows))

    def test_real_metadata_topology_capacity_and_placement(self):
        for isolation, main_nodes, expanded_nodes in (("docker",47,79), ("vm",271,527)):
            p = plan(isolation=isolation)
            self.assertEqual(p["parallel_node_count"], main_nodes)
            self.assertEqual(plan(isolation=isolation, profile="expanded")["parallel_node_count"], expanded_nodes)
            nodes = {n["id"]:n for n in p["resource_requirements"]}
            self.assertEqual(sum(n["role"]=="tikv-pd" for n in nodes.values()), 3)
            self.assertEqual(p["clusters"]["C1"]["metadata_backend"], "redis")
            for cluster, placements in p["placement_templates"].items():
                counts = Counter(row["executor_node"] for row in placements)
                self.assertTrue(set(counts) <= set(nodes))
                self.assertLessEqual(max(counts.values()), 8 if isolation=="docker" else 1)
                if cluster == "C4":
                    source={r["executor_node"] for r in placements if r["pool"]=="source"}
                    target={r["executor_node"] for r in placements if r["pool"]=="target"}
                    self.assertFalse(source & target)
            for cell in p["cells"]:
                if "capacity_floor" in cell:
                    backend=next(r for r in p["clusters"][cell["cluster_id"]]["roles"] if r["role"]=="rustfs")
                    self.assertGreaterEqual(sum(backend["data_disks_gib"])*1024**3,
                                            cell["capacity_floor"]["raw_data_only_with_25pct_free_bytes"])

    def test_empty_inventory_blocks_and_actual_allocations_validate(self):
        for provider in ("aliyun","provided"):
            p=plan(provider=provider)
            with self.assertRaises(ValueError): inventory.validate(inventory.from_plan(p, "a"*64))
            self.assertEqual(inventory.validate(allocated(p))["nodes"],47)
            value=allocated(p)
            self.assertFalse(inventory.validate(value)["hardware_verified"])
            for mutate in (
                lambda v:v["nodes"].pop(),
                lambda v:v["nodes"][1].update(resource_id=v["nodes"][0]["resource_id"]),
                lambda v:v["nodes"][0].update(host_key_verified=False),
                lambda v:v["nodes"][0].update(data_mounts=["/srv/experiment/sub"]),
                lambda v:v["nodes"][0]["requirements"].update(vcpus=1),
                lambda v:v.update(execution_enabled=True),
            ):
                bad=deepcopy(value);mutate(bad)
                with self.assertRaises(ValueError): inventory.validate(bad)

    def test_reject_ambiguous_scope(self):
        for kwargs in ({"profile":"pilot"},{"cluster":"C2","profile":"pilot"},
                       {"cluster":"C1","profile":"pilot","study":"writeback"},
                       {"include_llm_simulation":True},{"campaign":"unknown"}):
            with self.assertRaises(ValueError): scale.build(**dict({"campaign":"ecs-20261101"},**kwargs))
        self.assertEqual(scale.build()["total_cells"],618)  # frozen legacy reproducibility


if __name__ == "__main__":
    unittest.main()
