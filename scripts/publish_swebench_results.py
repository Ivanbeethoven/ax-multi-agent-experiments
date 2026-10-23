"""Copy completed SWE-bench evidence into this repo, omitting agent state and credentials."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def refresh_trace_hashes(root):
    for manifest in root.glob("*/trace/manifest.json"):
        data = json.loads(manifest.read_text(encoding="utf-8"))
        for name, metadata in data["files"].items():
            content = (manifest.parent / name).read_bytes()
            actual = hashlib.sha256(content).hexdigest()
            if metadata["sha256"] != actual:
                metadata["source_sha256_before_redaction"] = metadata["sha256"]
                metadata["sha256"] = actual
                metadata["bytes"] = len(content)
        data["verification_scope"] = "Original local files were compared before publication redaction; hashes describe published files."
        manifest.write_text(json.dumps(data, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--credential-config", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "results/swebench-verified-hard4-20261023")
    args = parser.parse_args()
    config = json.loads(args.credential_config.read_text(encoding="utf-8-sig"))
    secrets = [p.get("options", {}).get("apiKey", "") for p in config.get("provider", {}).values()]
    secrets = [s.encode() for s in secrets if s and not s.startswith("{")]
    manifest = json.loads((args.source_root / "selection.json").read_text())
    results = [json.loads((args.source_root / t["instance_id"] / "result.json").read_text()) for t in manifest["tasks"]]
    if args.output.exists():
        raise RuntimeError("Refusing to overwrite published evidence")
    args.output.mkdir(parents=True)
    redactions = {}
    def copy(src, relative):
        if not src.is_file():
            return
        data = src.read_bytes()
        count = 0
        for key in secrets:
            count += data.count(key)
            data = data.replace(key, b"[REDACTED_API_KEY]")
        dst = args.output / relative
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
        if count:
            redactions[str(relative)] = count
    copy(args.source_root / "selection.json", Path("selection.json"))
    copy(args.source_root / "environment.json", Path("environment.json"))
    files = ["image.json", "agent-setup.txt", "network-check.txt", "agent-events.jsonl", "agent-stderr.txt",
             "agent-summary.json", "prediction.patch", "prediction.json", "result.json", "controller-error.txt",
             "baseline/report.json", "gold/report.json", "grading/report.json", "grading/test_output.txt", "grading/patch-apply.txt"]
    for task in manifest["tasks"]:
        task_id = task["instance_id"]
        for rel in files:
            copy(args.source_root / task_id / rel, Path(task_id) / rel)
        for src in (args.source_root / task_id / "trace").rglob("*"):
            if src.is_file():
                copy(src, Path(task_id) / src.relative_to(args.source_root / task_id))
    # Preserve setup failures separately; never count them as independent model attempts.
    preflight = args.source_root / "preflight-attempts"
    if preflight.exists():
        for folder in preflight.iterdir():
            for rel in ["result.json", "controller-error.txt", "progress.json", "agent-events.jsonl"]:
                copy(folder / rel, Path("preflight-attempts") / folder.name / rel)
    total = {"visible_model_steps": 0, "reported_total_tokens": 0, "uncached_input_tokens": 0,
             "cache_read_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0}
    lines = ["# OpenCode + DeepSeek：较难 Verified 题", "", f"2026-10-23，本机 WSL / Docker 实跑。本目录每题一次尝试，步数上限：{manifest['max_steps'] or '不设限'}；单题超时：{manifest['timeout_s']} 秒；最多两题同时运行。原先限制 40 步的尝试与取消限制后的重跑分别保存，不能合并算作一次成功。", "",
             "题目按数据集的人类修复时间标签选择：四个仓库各取最高难度，再按 instance_id 字符串顺序取第一题。没有根据模型表现换题；这四题不能代表整个 Verified 的成功率。", "",
             "| 题目 | 难度标签 | 是否修复 | 模型步骤 | 工具调用 | Agent 用时（秒） |", "|---|---|---|---:|---:|---:|"]
    for task, result in zip(manifest["tasks"], results):
        agent = result.get("agent", {})
        status = "通过" if result["resolved"] else ("未通过" if result["status"] == "graded" else "未完成评分")
        lines.append(f"| {task['instance_id']} | {task['difficulty']} | {status} | {agent.get('model_steps','—')} | {agent.get('tool_calls','—')} | {agent.get('elapsed_s','—')} |")
        total["visible_model_steps"] += agent.get("model_steps", 0)
        for usage in agent.get("usage", []):
            token = usage["tokens"]
            total["reported_total_tokens"] += token.get("total", 0)
            total["uncached_input_tokens"] += token.get("input", 0)
            total["cache_read_tokens"] += token.get("cache", {}).get("read", 0)
            total["output_tokens"] += token.get("output", 0)
            total["reasoning_tokens"] += token.get("reasoning", 0)
    passed = sum(r["resolved"] for r in results)
    lines += ["", f"本目录 {passed}/{len(results)} 通过。Agent 自称完成和进程退出码不作成功依据；每份补丁在全新容器中应用数据集原始测试脚本，再由固定 SWE-bench 评分代码解析。", "",
              "每题开跑前都检查原始代码不能通过目标测试、参考修复能够通过。参考修复和评分补丁只在控制端/评分容器使用，没有挂给 Agent。Agent 可查看原始仓库及历史，但提示禁止查找上游答案，并关闭 webfetch/websearch/codesearch。容器使用 host 网络连接本机代理，这不是完全断网评测。", "",
              "准备期间出现列表字段解析错误、容器无法访问 localhost 代理；都发生在记录到模型步骤之前，单独保留在 preflight-attempts。镜像直拉因守护进程代理超时失败，改用 skopeo 通过现有代理导入，未重启既有容器。", "",
              f"可见模型步骤共 {total['visible_model_steps']}；报告总 tokens {total['reported_total_tokens']:,}，其中缓存读取 {total['cache_read_tokens']:,}。详细用量见 summary.json。自定义模型没有可靠价格信息，cost=0 不代表免费；账单费用未知，框架辅助请求也可能不在 step_finish 中。", "",
              "每题目录包含 Agent 事件、补丁、评分报告和测试输出。trace/ 保存原生会话导出及存在的长工具输出附件，manifest.json 记录校验摘要和采集范围。SQLite 数据库和缓存仍保留本机。没有采集原始模型 HTTP 请求，也没有采集 POSIX/FUSE 读写轨迹；这些日志不能直接用于 Rust 文件系统回放。没有将这些结果当作 BrewFS 性能数据，也没有在这次解题中插入迁移。", "",
              "复现入口为 scripts/run_swebench_opencode.py；完整输入、镜像标签、数据摘要、评分代码版本及限制见 selection.json。image.json 记录实际使用的本地镜像内容 ID；经 docker-daemon 导入后 RepoDigests 可能为空。", ""]
    (args.output / "README.md").write_text("\n".join(lines), encoding="utf-8")
    summary = {"resolved": passed, "attempted": len(results), "original_selected": 4, "results": [{"instance_id": r["instance_id"], "status": r["status"], "resolved": r["resolved"]} for r in results],
               "usage": total, "cost_usd": None, "credential_redactions": redactions}
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2))
    predictions = [json.loads((args.source_root / t["instance_id"] / "prediction.json").read_text())
                   for t in manifest["tasks"] if (args.source_root / t["instance_id"] / "prediction.json").exists()]
    (args.output / "predictions.jsonl").write_text("".join(json.dumps(p) + "\n" for p in predictions))
    for path in args.output.rglob("*"):
        if path.is_file() and any(key in path.read_bytes() for key in secrets):
            raise RuntimeError("Credential scan failed")
    refresh_trace_hashes(args.output)
    hashes = {p.relative_to(args.output).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in args.output.rglob("*") if p.is_file()}
    (args.output / "artifacts.sha256.json").write_text(json.dumps(hashes, indent=2))
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
