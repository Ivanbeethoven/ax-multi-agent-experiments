"""Print a small Markdown report from completed formal attempts on the coordinator."""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def fmt(value):
    return "—" if value is None else f"{value:,.3f}" if isinstance(value, float) else str(value)


def table(header, rows):
    print("| " + " | ".join(header) + " |")
    print("|" + "---|" * len(header))
    for row in rows:
        print("| " + " | ".join(map(fmt, row)) + " |")
    print()


def selected_cold_reads(path):
    cases = {}
    for row in load(path):
        key = Path(row["case"]).relative_to(path.parent).parts[:2]
        if key in cases or row["verified"] is not True:
            raise ValueError(f"Duplicate or unverified cold read: {key}")
        cases[key] = row
    systems = {"juicefs-before": "JuiceFS Redis（前）", "flat": "BrewFS Flat",
               "workspace": "BrewFS Workspace Native", "juicefs-after": "JuiceFS Redis（后）"}
    if set(cases) != {(op, name) for op in ("seqread", "randread") for name in systems}:
        raise ValueError("Expected eight cold reads from the same run")
    print("### 采用的冷读对照：A366 单机 zero-gap\n")
    print("2026-11-16 19:35–19:38，单机 RustFS 四个数据目录。顺序读 1 GiB、随机读 64 MiB；"
          "8 个 fio 作业，BrewFS 使用 8 个 Tokio 线程。吞吐量按应用读取字节除以 fio 子进程实际经过时间计算。"
          "8/8 项内容检查通过；本表不计入四节点正式完成数。\n")
    rows = []
    for op, label in (("seqread", "顺序"), ("randread", "随机")):
        for name, system in systems.items():
            row = cases[op, name]
            rows.append([label, system, row["bytes"] / 2**20 / row["seconds"],
                         row["p99_ms"], row["get_requests"], row["get_bytes"]])
    table(["负载", "系统", "MiB/s", "P99 ms", "GET 次数", "下载字节 B"], rows)
    print(f"原始记录：A366 `{path}`。\n")


def main(root):
    status = load(root / "status.json")["jobs"]
    groups = {}
    now = datetime.now(timezone(timedelta(hours=8)))
    print(f"# {root.name} 正式实验阶段结果\n\n截至北京时间 {now:%Y-%m-%d %H:%M}。"
          "每个条件仅取状态表选定且已通过的正式 attempt；原始结果保留在 A366。"
          f"表中末列相对于 `{root}`，该目录下的 `result.json` 是数据来源。\n")
    plan_jobs = load(root / "plan.json")["jobs"]
    for job in plan_jobs:
        group = job["group"]
        rows = groups.setdefault(group, [])
        state = status.get(job["id"], {})
        if state.get("state") != "passed":
            continue
        path = Path(state["output"])
        x = load(path / "result.json")
        if x["state"] != "passed" or x["validation_only"] or x["job_id"] != job["id"]:
            raise ValueError(f"Unexpected result: {path}")
        p = x["parameters"]
        row = [job["configuration"], p["system"]]
        if group == "01-io":
            app = x["application"]
            assert x["verification"]["passed"]
            row += [p["pattern"] + "/" + p["operation"], p["dynamic"], p["block_bytes"],
                    x["timing"]["app_done_seconds"],
                    app["fio_" + p["operation"] + "_throughput_mib_s"],
                    app["latency_us"]["p99_us"] / 1000]
        elif group == "02-fork":
            assert x["verification"]["passed"]
            row += [p["mode"], p["children"], x["fork_seconds"] if p["children"] else None,
                    x.get("read", {}).get("seconds"), x.get("write", {}).get("seconds")]
        elif group == "03-packed":
            if p["phase"] == "prepare":
                assert x["verification"]["passed"]
                row += [p["phase"], p["dynamic"], None, x["prepare_seconds"], None]
            else:
                readers = x["readers"]
                assert len(readers) == p["readers"] and all(v["loader"]["passed"] for v in readers)
                row += [p["phase"], p["dynamic"], p.get("packed_cache") if p["system"] == "brewfs-packed" else None,
                        x["seconds"], sum(v["loader"]["actual_read_bytes"] for v in readers)]
        elif group == "04-agent-replay":
            checks = [t["cross_host_verify"] for b in sorted(path.glob("batch-*/tasks.json")) for t in load(b)]
            assert len(checks) == x["completed_tasks"] and all(
                t["passed"] and t["source_host"] != t["verify_host"] for t in checks)
            row += [p["agents"], p["dynamic"], x["replay_seconds"], x["operations"],
                    x["actual_read_bytes"], x["actual_write_bytes"], len(checks)]
        else:
            raise ValueError(group)
        rows.append(row + [f"`{path.relative_to(root).as_posix()}`"])
    definitions = {
        "01-io": ("C1 · fio", ["负载", "Dynamic", "B（字节）", "应用耗时 s", "fio MiB/s", "P99 ms"]),
        "02-fork": ("C2 · fork 与 Overlay", ["模式", "子工作区数", "创建这批子工作区 s", "读取 s", "写入 s"]),
        "03-packed": ("C3 · 十万小文件", ["阶段", "Dynamic", "整包缓存", "阶段耗时 s", "读取字节 B"]),
        "04-agent-replay": ("C4 · Agent 回放", ["并发数", "Dynamic", "回放耗时 s", "操作次数", "读取 B", "写入 B", "跨机校验通过数"]),
    }
    notes = {
        "01-io": "表中吞吐使用 fio 自身的读或写带宽。"
                 "单机候选用例的计时方法及数据见[单独的诊断表](../../bench/cluster/diagnostics/read_path/README.md)。",
        "02-fork": "direct-parent 直接使用父工作区。未单列读写耗时的多子工作区条件保留其批量创建时间。",
        "03-packed": "准备、读取分别计时；只有完成的读取条件进入表中。四挂载点功能检查单独记录。",
        "04-agent-replay": "并发数 1 的一行包含依次执行的 8 个 trace。字节数为回放程序实际读写量，跨机校验在回放计时后进行。",
    }
    total = sum(map(len, groups.values()))
    c1_planned = sum(job["group"] == "01-io" for job in plan_jobs)
    c1 = ("已完成，Flat 采用已有结果" if root.name == "run-002" and len(groups["01-io"]) == c1_planned
          else f"{len(groups['01-io'])}/{c1_planned}")
    print(f"**本轮调度已完成 {total}/{len(plan_jobs)} 项**：C1 {c1}；C2 {len(groups['02-fork'])}/12，"
          f"C3 {len(groups['03-packed'])}/11，C4 {len(groups['04-agent-replay'])}/13。\n")
    for g, (label, columns) in definitions.items():
        print(f"## {label}\n")
        if g == "01-io" and root.name == "run-002":
            selected_cold_reads(root.parents[2] / "diagnostics/read-prefetch/quick-1g/zero-gap-compare/comparison.json")
            print("### 四节点正式 fio 已完成条件\n")
        print(notes[g] + "\n")
        table(["编号", "系统"] + columns + ["原始目录"], groups[g])


if __name__ == "__main__":
    main(Path(sys.argv[1]))
