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


def main(root):
    status = load(root / "status.json")["jobs"]
    groups = {}
    now = datetime.now(timezone(timedelta(hours=8)))
    print(f"# {root.name} 正式实验阶段结果\n\n截至北京时间 {now:%Y-%m-%d %H:%M}。"
          "每个条件仅取状态表选定且已通过的正式 attempt；原始结果保留在 A366。"
          f"表中末列相对于 `{root}`，该目录下的 `result.json` 是数据来源。\n")
    for job in load(root / "plan.json")["jobs"]:
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
        "01-io": "表中吞吐使用 fio 自身的读或写带宽。Flat 的四项扩展不在初始 52 项中；按修订计划共 56 项。"
                 "单机候选用例的计时方法及数据见[单独的诊断表](../../bench/cluster/diagnostics/read_path/README.md)。",
        "02-fork": "direct-parent 直接使用父工作区。未单列读写耗时的多子工作区条件保留其批量创建时间。",
        "03-packed": "准备、读取分别计时；只有完成的读取条件进入表中。四挂载点功能检查单独记录。",
        "04-agent-replay": "并发数 1 的一行包含依次执行的 8 个 trace。字节数为回放程序实际读写量，跨机校验在回放计时后进行。",
    }
    total = sum(map(len, groups.values()))
    print(f"**已完成 {total} 项**：C1 {len(groups['01-io'])}/20，C2 {len(groups['02-fork'])}/12，"
          f"C3 {len(groups['03-packed'])}/11，C4 {len(groups['04-agent-replay'])}/13。\n")
    for g, (label, columns) in definitions.items():
        print(f"## {label}\n\n{notes[g]}\n")
        table(["编号", "系统"] + columns + ["原始目录"], groups[g])


if __name__ == "__main__":
    main(Path(sys.argv[1]))
