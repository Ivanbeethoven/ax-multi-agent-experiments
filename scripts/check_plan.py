"""Validate a planning package and frozen inputs; never launches an experiment."""
from pathlib import Path
import csv, hashlib, json, re, sys
import jsonschema

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))
def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    errors, warnings = [], []
    def check(ok, message):
        if not ok: errors.append(message)
    docs = ["README.md","plan.zh.md","baselines.md","metrics.md","integration-and-migration.md","inputs/README.md",
            "replay-experiments.zh.md","agent-trace-and-resume.zh.md","execution-plan.zh.md","qualification.md"]
    required = docs + ["matrix.csv","workloads.json","run-manifest.example.json","events.schema.json","sources.json","report/main.tex","report/main.pdf","inputs/live-tasks.json","inputs/grading/index.json"]
    for f in required: check((ROOT/f).is_file(), "Missing "+f)
    if errors:
        print(json.dumps({"passed":False,"errors":errors})); return 1
    with (ROOT/"matrix.csv").open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    ids = {f"T{i}" for i in range(1,7)}
    check(len(rows)==6 and {r["experiment_id"] for r in rows}==ids, "Expected T1-T6 exactly once")
    for r in rows:
        check(r["status"]=="planned" and all(r.values()) and None not in r, "Invalid matrix row "+r["experiment_id"])
        check(set(r["rq"].split("|")) <= {"RQ1","RQ2","RQ3","RQ4"}, "Invalid RQ")
    w = read_json(ROOT/"workloads.json")
    check(w["schema_version"]==2 and w["status"]=="planned" and w["execution_enabled"] is False, "Not a v2 unexecuted plan")
    workloads = {x["id"]:x for x in w["workloads"]}
    fixtures = {x["id"]:x for x in w["fixtures"]}
    for r in rows:
        check(r["workloads"] in workloads, "Missing workload "+r["workloads"])
        x=workloads[r["workloads"]]
        check(r["experiment_id"] in x["experiments"], "Workload experiment mismatch")
        check(bool(x["outputs"]) and bool(x["oracle"]) and bool(x["negative"]), "Missing outputs/oracle/negative")
        check(set(x["fixtures"]) <= fixtures.keys(), "Unknown fixture")
    check({x["id"] for x in w["experiments"]}==ids, "JSON and CSV experiment mismatch")
    for x in w["experiments"]:
        row=next(r for r in rows if r["experiment_id"]==x["id"])
        check(set(x["questions"])==set(row["rq"].split("|")), "RQ mismatch "+x["id"])
    f1,f2=fixtures["F1"],fixtures["F2"]
    check(sum(f1[k]["count"]*f1[k]["bytes_each"] for k in ("small_files","large_files"))==f1["logical_data_bytes"]==2**30, "F1 size mismatch")
    check(f2["shard_count"]*f2["bytes_per_shard"]==f2["record_count"]*f2["record_bytes"]==f2["logical_data_bytes_per_version"]==2**30, "F2 size mismatch")
    check(len({(40503*j+17)%131072 for j in range(131072)})==131072,"Scattered write positions overlap")
    hold=workloads["W1"]["capacity_admission"]
    check(hold["admission_cutoff_s"]==120 and hold["hold_owner_until_s"]==180 and hold["verification_times_s"]==[120,180],"Capacity admission/hold mismatch")
    migration=workloads["W64"]
    check(migration["workspace_count"]==64 and migration["selected_workspace_count"]==16 and migration["unselected_workspace_count"]==48,"Migration population mismatch")
    check(migration["deadlines"]["first_useful_target_result_s"]==600 and migration["deadlines"]["paired_batch_end_s"]==900,"Migration deadline mismatch")
    schedule=migration["neighbor_schedule"]
    check(schedule["post_checkpoint_round_requests"]*schedule["interval_s"]==schedule["scheduled_window_s"]==600 and schedule["drain_limit_s"]==300 and schedule["per_round_request_deadline_s"]==120,"Neighbor schedule mismatch")
    p=w["pilots"]["deterministic"]
    batches=len(p["systems"])*len(p["concurrencies"])*len(p["cache_states"])*p["repetitions"]
    check(batches==p["planned_batches"]==12 and batches*p["workspaces_per_batch"]==p["planned_workspace_lifecycles"]==192,"Fixed-work pilot arithmetic mismatch")
    for name,v in [("calibration",w["pilots"]["live_calibration"]),("formal",w["live_budget"]["formal"])]:
        b=len(v["systems"])*len(v["concurrencies"])*v["batch_repetitions"]
        check(b==v["planned_batches"] and b*v["tasks_per_batch"]==v["planned_agent_executions"],name+" arithmetic mismatch")
    replay=w["replay_strategy"]
    budget=replay["initial_live_budget"]
    full=len(budget["validation_systems"])*budget["validation_batches_per_system"]*budget["validation_tasks_per_batch"]
    check(full==budget["validation_full_runs"]==32 and full+budget["capture_full_runs"]==budget["total_full_runs"]==48,
          "Replay-first full-run budget mismatch")
    check(budget["resume_tasks"]*len(budget["resume_systems"])*len(budget["resume_conditions"])==budget["continuation_attempts"]==8,
          "Continuation attempt budget mismatch")
    check(replay["model_calls_during_replay"]==0 and replay["a6_mode"]=="tool_replay" and replay["a6_replay_executions"]==192,
          "Replay model-call or A6 policy mismatch")
    check(w["live_budget"]["status"]=="legacy_optional_expansion", "Old model budget must not remain the default")
    from harness.generate_campaign import make_cells
    from harness.worker import consumer_keys
    for campaign, reps, count in [("ablations",3,102),("rq4-dirty-migration",1,18),
                                  ("rq1-sharing-boundary",3,24),("rq4-fault-qualification",1,19)]:
        cells=make_cells(campaign,repetitions=reps)
        check(len(cells)==count,"Supplement campaign count mismatch: "+campaign)
        check(all(c["status"]=="planned" and c["parameters"]["cache_protocol_id"] in w["run_rules"]["cache_protocols"] for c in cells),
              "Supplement cache/status mismatch: "+campaign)
    keys=list(consumer_keys(mode="scattered",profile="full"))
    check(len(keys)==len(set(keys))==1024 and len({key//4096 for key in keys})==64,"Scattered read coverage mismatch")
    modes=read_json(ROOT/"baseline-operation-contracts.json")
    check(modes.get("execution_enabled") is False,"Baseline mode file must not enable execution")
    m=read_json(ROOT/"run-manifest.example.json")
    check(m["status"]=="planned" and m["execution_enabled"] is False and m["measurements"] is None and m["commands_executed"] is None,"Manifest invents execution")
    check(all(v is None for v in m["executed_sources"].values()) and all(v is None for v in m["outcome"].values()),"Example observed fields must be null")
    schema=read_json(ROOT/"events.schema.json")
    jsonschema.Draft202012Validator.check_schema(schema)
    sample={"schema_version":2,"run_id":"schema-example","batch_id":"example","event_id":"example","source":"cloud_control","clock_domain":"coordinator","monotonic_ns":0,"kind":"attach_started","status":"begin"}
    jsonschema.validate(sample,schema)
    live_path=ROOT/"inputs/live-tasks.json"
    live=read_json(live_path)
    tasks=live["tasks"]
    check(len(tasks)==len({t["instance_id"] for t in tasks})==16,"Need 16 distinct tasks")
    check(len({t["base_commit"] for t in tasks})==16,"Preserve 16 original revisions")
    check(sha(live_path)==fixtures["L16"]["manifest_sha256"],"Frozen live manifest hash changed")
    for t in tasks:
        for key in ("prompt","problem_statement"):
            check(hashlib.sha256(t[key].encode("utf-8")).hexdigest()==t[key+"_sha256"],"Input hash mismatch "+t["instance_id"]+" "+key)
        check(re.fullmatch("[0-9a-f]{40}",t["base_commit"]) is not None,"Bad base commit")
        check(t["image_digest"] is None and t["runtime_qualification"]=="not_run","Unexpected runtime qualification")
    grading=read_json(ROOT/"inputs/grading/index.json")
    check(grading["gold_patches_exported"] is False,"Gold answers exported")
    verified=0
    for t in grading["tasks"]:
        oracle=read_json(ROOT/"inputs/grading"/t["oracle"])
        for key,hashkey in (("test_patch","test_patch_sha256"),("eval_script","eval_script_sha256")):
            path=ROOT/"inputs/grading"/t[key]
            check(sha(path)==oracle[hashkey],"Grading hash mismatch "+t["instance_id"]+" "+key)
            verified+=1
    # Historical sources are provenance, never current deployment qualification.
    historical=read_json(ROOT/"sources.json")
    for entry in historical["sources"]:
        repo=historical["repositories"][entry["repository"]]
        path=ROOT/repo["path_relative_to_plan"]/entry["path"]
        if not path.exists() or sha(path)!=entry["sha256"]:
            warnings.append("Historical checkout differs/unavailable: "+entry["source_id"])
    for name in docs:
        text=(ROOT/name).read_text(encoding="utf-8-sig")
        check("\ufffd" not in text,"Encoding corruption "+name)
        for match in re.finditer(r"\]\(([^)]+)\)",text):
            link=match.group(1)
            if "://" not in link and not link.startswith("#"):
                check((ROOT/name).parent.joinpath(link.split("#")[0]).exists(),"Broken local link "+name+" "+link)
    report={"passed":not errors,"scope":"planning consistency and frozen input integrity only; no cluster, model, grading, or performance run","experiments":len(rows),"research_questions":4,"baseline_ids":["B0","B1","B2","B3","B4"],"live_tasks":len(tasks),"grading_files_hash_verified":verified,"deterministic_pilot_batches":batches,"deterministic_pilot_lifecycles":192,"live_calibration_executions":96,"live_formal_executions":576,"source_audit":"historical only, never deployment status","event_schema":"Draft202012 checked and one non-observational in-memory shape validated","warnings":warnings,"errors":errors}
    report.update({"legacy_live_budget_status": "optional_not_current_default",
                   "current_proposed_full_agent_runs": budget["total_full_runs"],
                   "current_proposed_continuation_attempts": budget["continuation_attempts"],
                   "a6_replay_executions": replay["a6_replay_executions"]})
    (ROOT/"validation/local-checks.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return bool(errors)

if __name__=="__main__":
    sys.exit(main())

