#!/usr/bin/env python3
"""Validate complete trace evidence, then produce a concise Chinese report.

No synthetic observations are generated. ``--check`` never writes files.
``--render`` requires all three final results, matching C4 CI, verified
resource release and complete local comparison evidence. Local performance
acceptance is disclosed separately. The caller must inspect the exports.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import shlex
import subprocess
import sys
from typing import Any


EXPECTED = {
    "operations": 1_747_109,
    "read_bytes": 281_774_624,
    "write_bytes": 194_706_661,
    "remaining_handles": 0,
}
TRACE_SHA256 = "9953d90517eaf520814150f80abccb11f9d74b7ac84ecc56fa394a5f8495aa3d"
C4_SHA256 = "d609a429dfce0943114717495a76c164598df36a4564768fc346156548ea2783"
METHODS = ("native-overlay", "native-overlay-pack", "juicefs")
REQUIRED_CI = {
    "format", "shell", "check_workspace", "build_workspace", "test_workspace",
    "check_fuse_tokio", "check_fuse_io_uring", "clippy", "diff_check",
}
OPTIONAL_FIELDS = (
    "observer_cpu_seconds", "observer_cpu_percent_one_core",
    "observer_max_rss_kib", "observer_output_bytes", "host_collection_wall_seconds",
    "host_interval_s", "service_interval_s", "uploader_cpu_seconds",
    "cost_estimate_cny", "cost_scope", "additional_notes",
    "unmount_exit_code", "mount_exit_code", "mount_exited_naturally", "gc_budget_warnings",
)
REQUIRED_FIELDS = (
    "method_id", "run_id", "completed_workload", "completed", "execution_seconds", "operations",
    "read_bytes", "write_bytes", "remaining_handles", "mode", "exit_code",
    "timed_out", "expected_errors", "probe_errno_differences", "valid",
    "trace_sha256", "instance_type", "configuration", "fatal_error", "cleanup_errors",
)
RELEASE_FIELDS = (
    "instances_remaining", "created_disks_remaining", "own_security_group_remaining",
    "own_temporary_bucket_exists", "verified_utc",
)
MISSING = object()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def resolve_path(base: Path, value: str) -> Path:
    return (base / value).resolve()


def pointer(document: Any, spec: Any) -> Any:
    """RFC 6901 pointer; absent mapping is deliberately not a guessed default."""
    if not isinstance(spec, str) or not spec.startswith("/"):
        return MISSING
    current = document
    try:
        for part in spec[1:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            current = current[int(part)] if isinstance(current, list) else current[part]
        return current
    except (KeyError, IndexError, TypeError, ValueError):
        return MISSING


def number(value: Any, *, nonnegative: bool = True) -> bool:
    return (
        isinstance(value, (int, float)) and not isinstance(value, bool)
        and math.isfinite(value) and (not nonnegative or value >= 0)
    )


def exact_integer(value: Any, expected: int | None = None) -> bool:
    return type(value) is int and value >= 0 and (expected is None or value == expected)


def is_full_workspace_test(command: Any) -> bool:
    """Recognize real argv with harmless Cargo global/config/offline flags."""
    tokens = shlex.split(command) if isinstance(command, str) else command
    if not isinstance(tokens, list) or not tokens or not all(isinstance(item, str) for item in tokens):
        return False
    if Path(tokens[0]).stem != "cargo" or "test" not in tokens:
        return False
    args = tokens[tokens.index("test") + 1:]
    if not {"--workspace", "--lib", "--bins"}.issubset(args):
        return False
    flags = {"--workspace", "--lib", "--bins", "--offline", "--locked", "--frozen", "--release", "--quiet", "--verbose", "-q", "-v"}
    valued_options = {"--config", "--jobs", "-j", "--target", "--profile", "--target-dir", "--color", "--message-format"}
    index = 0
    while index < len(args):
        item = args[index]
        if item in valued_options:
            index += 2
            if index > len(args):
                return False
            continue
        if item not in flags and not any(item.startswith(option + "=") for option in valued_options):
            return False  # A positional test filter, selection flag or --no-run.
        index += 1
    return True


def source_link(path: Path, output: Path, label: str) -> str:
    relative = Path(os.path.relpath(path, output)).as_posix()
    return f"[{label}]({relative})"


def load_checked(manifest_path: Path) -> tuple[dict, list[dict], dict, list[str], set[Path]]:
    manifest = read_json(manifest_path)
    base = manifest_path.parent
    problems: list[str] = []
    inputs = {manifest_path.resolve(), Path(__file__).resolve()}
    methods = manifest.get("methods", [])
    if [item.get("method") for item in methods] != list(METHODS):
        problems.append("methods 必须按 native-overlay、native-overlay-pack、juicefs 顺序各列一次")
    normalized = []
    for spec in methods:
        method = spec.get("method", "unknown")
        prefix = f"{method}: "
        path = resolve_path(base, spec.get("result", ""))
        if not path.is_file():
            problems.append(prefix + f"结果不存在：{spec.get('result')}")
            continue
        inputs.add(path)
        document = read_json(path)
        if spec.get("mapping_verified") is not True:
            problems.append(prefix + "最终结果字段映射尚未核对")
        fields = spec.get("fields", {})
        required = list(REQUIRED_FIELDS)
        if method in METHODS[:2]:
            required.extend(["candidate", "source_archive_sha256"])
        values: dict[str, Any] = {}
        for key in required + list(OPTIONAL_FIELDS):
            value = pointer(document, fields.get(key))
            if value is MISSING:
                if key in required:
                    problems.append(prefix + f"缺少字段或映射 {key}")
                values[key] = None
            else:
                values[key] = value
        values.update(method=method, label=spec.get("label", method), source_path=path)
        if values["method_id"] != method:
            problems.append(prefix + "结果文件的 method 与该方法不一致")
        if not isinstance(values["run_id"], str) or not values["run_id"]:
            problems.append(prefix + "缺少可绑定资源释放证据的 run_id")
        values["configuration_note"] = spec.get("configuration_note", "")
        values["preparation_note"] = spec.get("preparation_note", "准备阶段未记录，无法拆分。")
        values["preparation_stages"] = []
        for stage in spec.get("preparation_stages", []):
            seconds = pointer(document, stage.get("seconds_pointer"))
            if seconds is MISSING:
                seconds = None
            elif not number(seconds):
                problems.append(prefix + f"准备计时不是有限非负数：{stage.get('label')}")
            values["preparation_stages"].append({"label": stage.get("label", "未命名阶段"), "seconds": seconds})
        if not values["configuration_note"] or values["configuration_note"].startswith("待"):
            problems.append(prefix + "缺少已核实的实例/缓存配置说明")
        if values["completed_workload"] is not True or values["completed"] is not True:
            problems.append(prefix + "未确认完整回放")
        for field, target in EXPECTED.items():
            if not exact_integer(values[field], target):
                problems.append(prefix + f"{field} 应为 {target}，实际 {values[field]!r}")
        if not number(values["execution_seconds"]) or values["execution_seconds"] <= 0:
            problems.append(prefix + "execution_seconds 必须为有限正数")
        if values["mode"] != "performance":
            problems.append(prefix + "回放模式不是 performance")
        if type(values["exit_code"]) is not int or values["exit_code"] != 0:
            problems.append(prefix + "回放退出码不是整数 0")
        if values["timed_out"] is not False:
            problems.append(prefix + "缺少 timed_out=false 证据")
        for key in ("expected_errors", "probe_errno_differences"):
            if not exact_integer(values[key]):
                problems.append(prefix + f"{key} 必须保留非负整数观测值")
        if type(values["valid"]) is not bool:
            problems.append(prefix + "缺少原始 valid 标志")
        if values["trace_sha256"] != TRACE_SHA256:
            problems.append(prefix + "trace SHA256 与目标 trace 不一致")
        if method in METHODS[:2]:
            if values.get("candidate") != "C4":
                problems.append(prefix + "不是最终 C4")
            if values.get("source_archive_sha256") != C4_SHA256:
                problems.append(prefix + "C4 源码归档 SHA256 不一致")
        if not isinstance(values["instance_type"], str) or not values["instance_type"]:
            problems.append(prefix + "缺少实例类型")
        if not isinstance(values["configuration"], dict) or not values["configuration"]:
            problems.append(prefix + "缺少原始配置对象")
        if values["fatal_error"] is not None:
            problems.append(prefix + f"fatal_error 非空：{values['fatal_error']!r}")
        if values["cleanup_errors"] != []:
            problems.append(prefix + "清理错误不是空数组")
        for key in OPTIONAL_FIELDS:
            if key.endswith(("_seconds", "_kib", "_bytes", "_cny", "_one_core", "_interval_s")):
                if values[key] is not None and not number(values[key]):
                    problems.append(prefix + f"{key} 必须为有限非负数或缺省")

        release_spec = spec.get("release", {})
        release_path = resolve_path(base, release_spec.get("result", spec.get("result", "")))
        release = document if release_path == path else None
        if release is None and release_path.is_file():
            release = read_json(release_path)
            inputs.add(release_path)
        if release is None:
            problems.append(prefix + "资源释放记录不存在")
            release = {}
        if release_path != path:
            release_run_id = pointer(release, release_spec.get("fields", {}).get("run_id"))
            if release_run_id is MISSING or release_run_id != values["run_id"]:
                problems.append(prefix + "独立释放核验的 run_id 未与本次结果绑定")
        release_values = {}
        for key in RELEASE_FIELDS:
            value = pointer(release, release_spec.get("fields", {}).get(key))
            if value is MISSING:
                problems.append(prefix + f"缺少资源释放核验字段 {key}")
                value = None
            release_values[key] = value
        for key in RELEASE_FIELDS[:3]:
            if not exact_integer(release_values[key], 0):
                problems.append(prefix + f"{key} 尚未核验为 0")
        if release_values["own_temporary_bucket_exists"] is not False:
            problems.append(prefix + "临时桶尚未核验为不存在")
        if not isinstance(release_values["verified_utc"], str) or not release_values["verified_utc"]:
            problems.append(prefix + "缺少释放核验时间")
        else:
            try:
                verified = datetime.fromisoformat(release_values["verified_utc"].replace("Z", "+00:00"))
                if verified.utcoffset() is None:
                    raise ValueError("timezone absent")
            except ValueError:
                problems.append(prefix + "释放核验时间不是带时区的 ISO 8601 值")
        values["release"] = release_values
        values["release_path"] = release_path
        normalized.append(values)

    acceptance_path = resolve_path(base, manifest.get("acceptance_evidence", ""))
    acceptance: dict = {}
    if not acceptance_path.is_file():
        problems.append("缺少 C4 验收证据 JSON")
    else:
        inputs.add(acceptance_path)
        acceptance = read_json(acceptance_path)
        if acceptance.get("candidate") != "C4" or acceptance.get("source_archive_sha256") != C4_SHA256:
            problems.append("CI 证据未绑定当前 C4 源码归档")
        if acceptance.get("complete") is not True or acceptance.get("passed") is not True:
            problems.append("C4 本地 CI 尚未完整通过")
        checks = acceptance.get("checks", [])
        names = {check.get("category", check.get("name")) for check in checks if isinstance(check, dict)}
        if not REQUIRED_CI.issubset(names):
            problems.append("CI 缺少检查项：" + ", ".join(sorted(REQUIRED_CI - names)))
        for check in checks:
            if check.get("passed") is not True or type(check.get("exit_code")) is not int or check["exit_code"] != 0:
                problems.append(f"CI 检查未通过：{check.get('name')}")
            if not check.get("command") or not check.get("evidence"):
                problems.append(f"CI 缺少命令/证据引用：{check.get('name')}")
            elif isinstance(check["evidence"], str):
                evidence_path = resolve_path(acceptance_path.parent, check['evidence'].partition('#')[0])
                if not evidence_path.is_file():
                    problems.append(f"CI 证据引用不存在：{check.get('name')}")
                else:
                    inputs.add(evidence_path)
        workspace_checks = [check for check in checks if check.get("category", check.get("name")) == "test_workspace"]
        if not workspace_checks or not any(is_full_workspace_test(check.get("command")) for check in workspace_checks):
            problems.append("缺少完整 workspace test 命令证据")
        compose = acceptance.get("compose", {})
        if compose.get("complete") is not True or not compose.get("evidence"):
            problems.append("compose 尚未完整执行并保存原始结果证据")
        if type(compose.get("passed")) is not bool:
            problems.append("compose 缺少原始命令／检查通过状态")
        if type(compose.get("performance_accepted")) is not bool or not compose.get("performance_summary"):
            problems.append("compose 缺少明确的性能验收状态与回退说明")
        if compose.get("source_archive_sha256") != C4_SHA256:
            problems.append("compose 门禁未绑定当前 C4 源码归档")
        if compose.get("evidence"):
            compose_path = resolve_path(acceptance_path.parent, compose['evidence'].partition('#')[0])
            if not compose_path.is_file():
                problems.append("compose 验收证据引用不存在")
            else:
                inputs.add(compose_path)
        if acceptance.get("owner_acceptance", {}).get("render_authorized") is not True:
            problems.append("尚未收到 root 对 CI、compose 与云端最终结果的渲染放行")
        if not acceptance.get("fixes") or not acceptance.get("limitations"):
            problems.append("验收证据必须保留修复范围与结论限制")
    acceptance["source_path"] = acceptance_path
    for history in manifest.get("history", []):
        path = resolve_path(base, history.get("result", ""))
        if not path.is_file():
            problems.append(f"历史记录不存在：{history.get('result')}")
        else:
            inputs.add(path)
    return manifest, normalized, acceptance, problems, inputs


def format_number(value: Any, digits: int = 2) -> str:
    return "未记录" if value is None else f"{value:,.{digits}f}"


def table_cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", "；")


def report_text(manifest: dict, rows: list[dict], acceptance: dict, base: Path, out: Path) -> str:
    compose = acceptance["compose"]
    performance_notice = (
        "本地性能验收已通过，原始比较结果仍完整保留。"
        if compose["performance_accepted"] else
        "**" + compose.get("performance_notice", "本地纯写回退待复核，候选尚未通过性能验收。") + "**"
    )
    lines = [
        "# 完整 AI trace 回放测试摘要", "",
        "三种最终方法均完成同一条 trace 的 1,747,109 次操作，读取 281,774,624 B、写入 194,706,661 B，剩余句柄 0、回放退出码 0，未超时。对应实例、创建磁盘、安全组和临时桶均已核验释放。", "",
        performance_notice + " " + compose["performance_summary"] + " 命令成功不等于没有性能回退。", "",
        "每方法仅一次串行 performance 回放（n=1）；不同实例与缓存配置下的耗时观测不构成公平排名、加速比或严格正确性证明。", "",
        "![三种最终方法的完整回放耗时](replay-seconds.png)", "",
        "| 方法 | 完整耗时（s） | 预期错误 | probe errno 差异 | 退出：回放/卸载/挂载 | 原始 valid | 数据 |",
        "|---|---:|---:|---:|---|---|---|",
    ]
    for row in rows:
        exits = "/".join("未记录" if row[key] is None else str(row[key]) for key in ("exit_code", "unmount_exit_code", "mount_exit_code"))
        if row['mount_exited_naturally'] is True:
            exits += "（挂载自然退出）"
        lines.append(f"| {row['label']} | {row['execution_seconds']:,.2f} | {row['expected_errors']:,} | {row['probe_errno_differences']:,} | {exits} | {str(row['valid']).lower()} | {source_link(row['source_path'], out, '结果 JSON')} |")
    lines += ["", "工作量完整与字节数一致只验证回放完整性；预期错误和 probe errno 差异按原报告保留。未做严格最终状态认证。", "",
              "| 方法 | 实例与缓存配置 | 回放前准备（s） |", "|---|---|---|"]
    for row in rows:
        timings = "；".join(f"{stage['label']}：{format_number(stage['seconds'])}" for stage in row['preparation_stages']) or "未记录，无法拆分"
        lines.append(f"| {row['label']} | {table_cell(row['instance_type'])}；{table_cell(row['configuration_note'])} | {table_cell(timings)}；{table_cell(row['preparation_note'])} |")
    for paragraph in manifest.get("configuration_context", []):
        lines += ["", paragraph]
    lines += ["", "固定版本的 pack／snapshot fixture 构建、上传和挂载等准备阶段与图中的纯回放耗时分开。未记录的阶段不由总时间相减估算；这些准备不是在线 freeze/fork/迁移测试，多 agent fork/迁移实验尚未完成。"]
    lines += ["", "修复与验证：" + "；".join(acceptance["fixes"]) + "。",
              f"C4 本地 CI 与源码差异检查全部通过，包含完整 workspace test；compose 已完整执行，原始命令／检查 passed={str(compose['passed']).lower()}，性能验收与之分开记录。源码归档 `{C4_SHA256[:12]}` 与两次 C4 回放一致。" + source_link(acceptance["source_path"], out, "CI、compose 原始结果与修复证据") + "。", "",
              "| 方法 | 观测器 CPU（s） | 单核占用（%） | 观测器峰值 RSS（MiB） | 上传器 CPU（s） | 费用估算（元） |",
              "|---|---:|---:|---:|---:|---:|"]
    for row in rows:
        rss = None if row['observer_max_rss_kib'] is None else row['observer_max_rss_kib'] / 1024
        lines.append(f"| {row['label']} | {format_number(row['observer_cpu_seconds'])} | {format_number(row['observer_cpu_percent_one_core'])} | {format_number(rss)} | {format_number(row['uploader_cpu_seconds'])} | {format_number(row['cost_estimate_cny'], 4)} |")
    lines += ["", "观测器和上传器的 CPU/RSS 是资源观测，不等于对回放延迟的影响；未做关闭观测器的成对试验。费用为记录中的估算，不是账单，不补算缺失项。"]
    scopes = list(dict.fromkeys(str(row['cost_scope']) for row in rows if row['cost_scope']))
    if scopes:
        lines.append("费用范围：" + "；".join(scopes) + "。")
    lines.append("释放证据：" + "、".join(source_link(row['release_path'], out, row['label']) for row in rows) + "。")
    swap = acceptance.get("local_build_swap_cleanup", {})
    if swap.get("inactive") is True and swap.get("removed") is True:
        lines.append("本地临时构建 swap 也已核验停用并删除；该记录独立于各云端实例释放。")
    warnings = [f"{row['label']}：GC metadata-budget 警告 {row['gc_budget_warnings']} 次" for row in rows if row['gc_budget_warnings']]
    if warnings:
        lines.append("保留的额外警告：" + "；".join(warnings) + "。")
    if manifest.get("history"):
        links = [source_link(resolve_path(base, item['result']), out, item['label']) for item in manifest['history']]
        lines += ["", "历史排除仅包括原 packed EBUSY／早期超时、JuiceFS 系统重启中断及 C1 版本差异；旧候选与故障注入只作诊断／回归证据，不计入最终耗时。详细记录：" + "、".join(links) + "。"]
    lines += ["", "限制：" + "；".join(acceptance['limitations']) + "。", "",
              "[源数据 CSV](source-data.csv) · [可编辑 SVG](replay-seconds.svg) · [PDF](replay-seconds.pdf) · [输入与输出哈希](provenance.json)", ""]
    return "\n".join(lines)


def write_source_csv(rows: list[dict], out: Path) -> None:
    fields = ["method", "label", "execution_seconds", "operations", "read_bytes", "write_bytes", "remaining_handles", "exit_code", "expected_errors", "probe_errno_differences", "valid", "instance_type", "configuration_note", "source_json", "source_sha256", "source_pointer", "n"]
    with (out / "source-data.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            record = {key: row[key] for key in fields if key in row}
            record.update(source_json=Path(os.path.relpath(row['source_path'], out)).as_posix(), source_sha256=sha256(row['source_path']), source_pointer=row['seconds_pointer'], n=1)
            writer.writerow(record)


def render_figure(rows: list[dict], out: Path, skill_scripts: Path, acceptance: dict) -> None:
    import matplotlib as mpl
    mpl.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    sys.path.insert(0, str(skill_scripts))
    alignment = importlib.import_module("audit_panel_alignment")
    available = {font.name for font in font_manager.fontManager.ttflist}
    cjk = next((name for name in ("Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "Source Han Sans SC") if name in available), None)
    if cjk is None:
        raise RuntimeError("缺少中文字体：安装 Microsoft YaHei 或 Noto Sans CJK SC 后再渲染")
    mpl.rcParams.update({
        "font.family": "sans-serif", "font.sans-serif": [cjk, "Arial", "DejaVu Sans"],
        "font.size": 8, "axes.labelsize": 8, "xtick.labelsize": 8, "ytick.labelsize": 8,
        "svg.fonttype": "none", "pdf.fonttype": 42, "axes.unicode_minus": False,
        "axes.spines.top": False, "axes.spines.right": False, "axes.spines.left": False,
        "axes.linewidth": 0.7, "figure.facecolor": "white", "savefig.facecolor": "white",
    })
    fig, ax = plt.subplots(figsize=(183 / 25.4, 88 / 25.4))
    fig.subplots_adjust(left=0.31, right=0.96, bottom=0.31, top=0.91)
    seconds = [row['execution_seconds'] for row in rows]
    # Fixed method order; these are individual observations, not group means.
    ax.barh(range(3), seconds, height=0.52, color=["#527C95", "#638EAD", "#92999F"], edgecolor="none")
    ax.set_yticks(range(3), [row['label'] for row in rows])
    ax.invert_yaxis()
    ax.tick_params(axis="y", length=0, pad=8)
    ax.set_xlim(0, max(seconds) * 1.08)
    ax.set_xlabel("完整回放耗时（秒）", labelpad=7)
    step = max(1, int(math.ceil(max(seconds) / 5 / 100)) * 100)
    ax.set_xticks(range(0, int(max(seconds) * 1.08) + 1, step))
    ax.xaxis.grid(True, color="#E3E6E8", linewidth=0.6)
    ax.set_axisbelow(True)
    fig.text(0.31, 0.965, "同一完整 trace 的单次观测", fontsize=9, ha="left", va="top")
    fig.text(0.035, 0.14, "n=1 / 方法；100 GiB PL0；独立实例、缓存与日志落盘位置不同。", fontsize=7, ha="left", va="top")
    performance_caption = (
        "仅作开发诊断；e 实例上限、原始比较与严格性限制见报告。"
        if acceptance['compose']['performance_accepted'] else
        "开发诊断；纯写顺序敏感、直接随机写墙钟开销待归因；性能未验收。"
    )
    fig.text(0.035, 0.075, performance_caption, fontsize=7, ha="left", va="top")
    fig.canvas.draw()
    alignment.require_matplotlib_panel_alignment(fig, json_out=str(out / "qa" / "alignment.json"), overlay_svg=str(out / "qa" / "alignment.svg"), tolerance_pt=1.5, gutter_tolerance_pt=1.5, strict=True)
    # Keep the contracted page size; do not crop then rescale the text.
    fig.savefig(out / "replay-seconds.svg")
    fig.savefig(out / "replay-seconds.pdf")
    fig.savefig(out / "replay-seconds.png", dpi=600)
    plt.close(fig)


def audit_command(command: list[str], log: Path) -> None:
    result = subprocess.run(command, text=True, encoding="utf-8", errors="replace", capture_output=True)
    log.write_text(result.stdout + result.stderr, encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(f"QA 未通过：{Path(command[1]).name}，见 {log.name}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--check", action="store_true", help="只校验，默认行为；不写任何文件")
    parser.add_argument("--render", action="store_true", help="完整性门槛通过后才渲染并生成报告")
    parser.add_argument("--skill-scripts", type=Path, help="nature-figure 的 scripts 目录，渲染必需")
    args = parser.parse_args()
    if args.check and args.render:
        parser.error("--check 和 --render 不能同时使用")
    manifest_path = args.manifest.resolve()
    manifest, rows, acceptance, problems, inputs = load_checked(manifest_path)
    if problems:
        print(json.dumps({"status": "pending", "outputs_written": False, "issues": problems}, ensure_ascii=False, indent=2))
        return 2
    if not args.render:
        print(json.dumps({"status": "ready", "outputs_written": False, "methods": list(METHODS)}, ensure_ascii=False, indent=2))
        return 0
    if not args.skill_scripts or not args.skill_scripts.is_dir():
        parser.error("--render 需要有效的 --skill-scripts 路径")
    # Detect missing dependencies before creating any output directory.
    importlib.import_module("matplotlib")
    importlib.import_module("pymupdf")
    out = (args.out or manifest_path.parent / "output").resolve()
    out.mkdir(parents=True, exist_ok=True)
    qa = out / "qa"
    qa.mkdir(exist_ok=True)
    audit_command([sys.executable, str(args.skill_scripts / "validate_figure.py"), str(Path(__file__).resolve())], qa / "source-audit.txt")
    for row, spec in zip(rows, manifest["methods"], strict=True):
        row['seconds_pointer'] = spec['fields']['execution_seconds']
    write_source_csv(rows, out)
    render_figure(rows, out, args.skill_scripts.resolve(), acceptance)
    audit_command([sys.executable, str(args.skill_scripts / "audit_pdf_text.py"), str(out / "replay-seconds.pdf"), "--min-pt", "5", "--json"], qa / "pdf-text.json")
    audit_command([sys.executable, str(args.skill_scripts / "audit_figure_collisions.py"), str(out / "replay-seconds.pdf"), "--json-out", str(qa / "collision-audit.json"), "--overlay-pdf", str(qa / "collision-audit.pdf")], qa / "collision-audit.txt")
    svg = (out / "replay-seconds.svg").read_text(encoding="utf-8")
    if "<text" not in svg:
        raise RuntimeError("SVG 未保留可编辑文字")
    (out / "summary.zh.md").write_text(report_text(manifest, rows, acceptance, manifest_path.parent, out), encoding="utf-8")
    provenance = {
        "status": "automated_checks_finished_visual_review_required",
        "n_per_method": 1, "error_bars": "none: one observation per method",
        "strict_correctness_certified": False, "fair_cross_host_ranking": False,
        "local_performance_accepted": acceptance['compose']['performance_accepted'],
        "local_performance_summary": acceptance['compose']['performance_summary'],
        "exclusions": manifest.get("history", []),
        "input_sha256": {Path(os.path.relpath(path, out)).as_posix(): sha256(path) for path in sorted(inputs)},
        "output_sha256": {path.name: sha256(path) for path in out.iterdir() if path.is_file() and path.name != "provenance.json"},
    }
    (out / "provenance.json").write_text(json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": provenance['status'], "out": str(out), "manual_action": "逐项检查 QA WARN，并按最终物理尺寸检查 PNG/PDF/SVG；完成前不得宣称最终 QA 通过。"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    for console in (sys.stdout, sys.stderr):
        if hasattr(console, "reconfigure"):
            console.reconfigure(encoding="utf-8")
    try:
        raise SystemExit(main())
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, ImportError) as error:
        print(json.dumps({"status": "blocked", "error": str(error)}, ensure_ascii=False, indent=2), file=sys.stderr)
        raise SystemExit(1)
