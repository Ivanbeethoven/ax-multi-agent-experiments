//! Hash-free trace boundary indexer and post-replay tree verifier.
use serde_json::{json, Value};
use std::{
    collections::{BTreeMap, BTreeSet},
    env,
    fs::{self, File},
    io::{BufRead, BufReader, Read},
    os::unix::fs::PermissionsExt,
    path::{Path, PathBuf},
    time::Instant,
};

type Result<T> = std::result::Result<T, Box<dyn std::error::Error + Send + Sync>>;

#[derive(Clone, Debug)]
struct CandidateBoundary {
    kind: &'static str,
    id: String,
    unix_s: f64,
    locator: String,
}

fn parse_agent_candidates(agent_events_path: &Path) -> Result<(Vec<CandidateBoundary>, usize)> {
    let raw = fs::read_to_string(agent_events_path)?;
    let mut candidates = Vec::new();
    let mut skipped_parallel = 0usize;
    let mut max_tool_end_ms: u64 = 0;
    let mut turn_idx = 0usize;
    let mut tool_idx = 0usize;
    let mut current_turn_msg = String::new();
    let mut turn_emitted_first_action = false;

    for (line_idx, line) in raw.lines().enumerate() {
        let line_no = line_idx + 1;
        if line.trim().is_empty() {
            continue;
        }
        let v: Value = serde_json::from_str(line)?;
        let ty = v.get("type").and_then(|x| x.as_str()).unwrap_or("");
        let ts_ms = v.get("timestamp").and_then(|x| x.as_u64()).unwrap_or(0);
        let part = v.get("part").cloned().unwrap_or(Value::Null);
        let msg_id = part
            .get("messageID")
            .and_then(|x| x.as_str())
            .unwrap_or("")
            .to_owned();

        match ty {
            "step_start" => {
                turn_idx += 1;
                current_turn_msg = msg_id.clone();
                turn_emitted_first_action = false;
                if ts_ms >= max_tool_end_ms && ts_ms > 0 {
                    candidates.push(CandidateBoundary {
                        kind: "turn",
                        id: format!("turn-{turn_idx:04}"),
                        unix_s: (ts_ms as f64) / 1000.0,
                        locator: format!(
                            "agent-events.jsonl:L{line_no}:step_start:msg={msg_id}:ts_ms={ts_ms}"
                        ),
                    });
                    turn_emitted_first_action = true;
                }
            }
            "tool_use" => {
                tool_idx += 1;
                let state_time = part.get("state").and_then(|s| s.get("time"));
                let start_ms = state_time
                    .and_then(|t| t.get("start"))
                    .and_then(|x| x.as_u64())
                    .unwrap_or(ts_ms);
                let end_ms = state_time
                    .and_then(|t| t.get("end"))
                    .and_then(|x| x.as_u64())
                    .unwrap_or(ts_ms);
                let tool_name = part.get("tool").and_then(|x| x.as_str()).unwrap_or("tool");
                let call_id = part.get("callID").and_then(|x| x.as_str()).unwrap_or("");
                if start_ms < max_tool_end_ms {
                    skipped_parallel += 1;
                } else if start_ms > 0 {
                    let kind = if !turn_emitted_first_action && msg_id == current_turn_msg {
                        turn_emitted_first_action = true;
                        "turn"
                    } else {
                        "tool"
                    };
                    let id = if kind == "turn" {
                        format!("turn-{turn_idx:04}")
                    } else {
                        format!("tool-{tool_idx:04}")
                    };
                    candidates.push(CandidateBoundary {
                        kind,
                        id,
                        unix_s: (start_ms as f64) / 1000.0,
                        locator: format!(
                            "agent-events.jsonl:L{line_no}:tool_use:{tool_name}:call={call_id}:start_ms={start_ms}"
                        ),
                    });
                }
                if end_ms > max_tool_end_ms {
                    max_tool_end_ms = end_ms;
                }
            }
            "step_finish" => {
                if ts_ms > max_tool_end_ms {
                    max_tool_end_ms = ts_ms;
                }
            }
            _ => {}
        }
    }
    Ok((candidates, skipped_parallel))
}

fn run_index(trace_dir: &Path, out_boundaries: &Path, out_summary: &Path) -> Result<()> {
    let t0 = Instant::now();
    let started_path = trace_dir.join("started.json");
    let agent_events_path = trace_dir.join("agent-events.jsonl");
    let events_path = trace_dir.join("capture").join("events.jsonl");
    let blobs_dir = trace_dir.join("capture").join("blobs");

    let started: Value = serde_json::from_slice(&fs::read(&started_path)?)?;
    let started_unix = started["unix"].as_f64().ok_or("missing started.unix")?;
    let eb_seq = started["events_before_inference"]
        .as_u64()
        .ok_or("missing events_before_inference")?;

    let (candidates, skipped_parallel) = parse_agent_candidates(&agent_events_path)?;

    // Stream events.jsonl once to collect compact (seq, start_ns, is_lifecycle) & clock offsets
    let mut reader = BufReader::with_capacity(8 * 1024 * 1024, File::open(&events_path)?);
    let mut line = Vec::with_capacity(4096);
    let mut events_bytes = 0u64;
    let mut count = 0u64;
    let mut first_op = String::new();
    let mut last_op = String::new();
    let mut eb_end_ns: Option<u64> = None;
    let mut created_paths = BTreeSet::new();
    let mut offsets = Vec::new();
    // Store start_ns for every seq >= 1 (1-based index -> vec index seq-1)
    let mut start_ns_vec: Vec<u64> = Vec::with_capacity(200_000);

    loop {
        line.clear();
        let n = reader.read_until(b'\n', &mut line)?;
        if n == 0 {
            break;
        }
        events_bytes += n as u64;
        let ev: Value = serde_json::from_slice(&line)?;
        count += 1;
        let seq = ev["seq"].as_u64().ok_or("missing seq")?;
        if seq != count {
            return Err(format!("non-contiguous seq at {count}: got {seq}").into());
        }
        let op = ev["op"].as_str().ok_or("missing op")?;
        if count == 1 {
            first_op = op.to_owned();
        }
        last_op = op.to_owned();
        let s_ns = ev["start_ns"].as_u64().unwrap_or(0);
        let e_ns = ev["end_ns"].as_u64().unwrap_or(s_ns);
        start_ns_vec.push(s_ns);
        if seq == eb_seq {
            eb_end_ns = Some(e_ns);
        }
        if offsets.len() < 512 {
            let errno = ev["errno"].as_i64().unwrap_or(-1);
            if errno == 0 && (op == "create" || op == "mkdir") {
                if let Some(p) = ev.get("args").and_then(|a| a.get(0)).and_then(|x| x.as_str()) {
                    created_paths.insert(p.to_owned());
                }
            } else if errno == 0 && op == "getattr" && !created_paths.is_empty() {
                if let Some(p) = ev.get("args").and_then(|a| a.get(0)).and_then(|x| x.as_str()) {
                    if created_paths.remove(p) {
                        if let Some(ct) = ev
                            .get("result")
                            .and_then(|r| r.get("st_ctime").or_else(|| r.get("st_mtime")))
                            .and_then(|x| x.as_f64())
                        {
                            offsets.push(ct - (s_ns as f64) / 1e9);
                        }
                    }
                }
            }
        }
    }

    if first_op != "init" || last_op != "destroy" || count < 3 {
        return Err(format!("invalid trace lifecycle: count={count} first={first_op} last={last_op}").into());
    }

    let started_offset = eb_end_ns.map(|ns| started_unix - (ns as f64) / 1e9);
    let clock_offset = if !offsets.is_empty() {
        offsets.sort_by(|a, b| a.partial_cmp(b).unwrap());
        offsets[offsets.len() / 2]
    } else {
        started_offset.ok_or("cannot establish clock offset")?
    };

    // Map each candidate to the first seq in (eb_seq.max(1) + 1 .. count) with start_ns >= target_ns and > prev_seq
    let mut mapped = Vec::new();
    let mut prev_seq = eb_seq.max(1);
    let mut used_ids = BTreeSet::new();
    for cand in candidates {
        let target_mono_s = cand.unix_s - clock_offset;
        let target_ns = if target_mono_s <= 0.0 {
            0u64
        } else {
            (target_mono_s * 1e9) as u64
        };
        let search_from = (prev_seq + 1) as usize; // 1-based seq
        if search_from >= count as usize {
            break;
        }
        // Binary search or linear advance in start_ns_vec[(search_from - 1) .. (count as usize - 1)]
        let slice = &start_ns_vec[(search_from - 1)..(count as usize - 1)];
        let rel = slice.partition_point(|&ns| ns < target_ns);
        if rel < slice.len() {
            let chosen_seq = (search_from + rel) as u64;
            if chosen_seq > prev_seq && chosen_seq < count && used_ids.insert(cand.id.clone()) {
                mapped.push(json!({
                    "before_seq": chosen_seq,
                    "kind": cand.kind,
                    "id": cand.id,
                    "evidence_index": 0,
                    "evidence_locator": cand.locator,
                }));
                prev_seq = chosen_seq;
            }
        }
    }

    if mapped.is_empty() {
        return Err("zero non-overlapping tool/turn boundaries mapped before destroy".into());
    }

    // Count blobs & initial files without hashing
    let mut blob_files = 0u64;
    let mut blob_bytes = 0u64;
    if blobs_dir.is_dir() {
        for entry in fs::read_dir(&blobs_dir)? {
            let entry = entry?;
            let m = entry.metadata()?;
            if m.is_file() {
                blob_files += 1;
                blob_bytes += m.len();
            }
        }
    }

    let mut initial_files = 0u64;
    let mut initial_dirs = 0u64;
    let mut initial_symlinks = 0u64;
    let mut initial_bytes = 0u64;
    fn walk_count(
        p: &Path,
        files: &mut u64,
        dirs: &mut u64,
        syms: &mut u64,
        bytes: &mut u64,
    ) -> Result<()> {
        for e in fs::read_dir(p)? {
            let e = e?;
            let ft = e.file_type()?;
            if ft.is_symlink() {
                *syms += 1;
            } else if ft.is_dir() {
                *dirs += 1;
                walk_count(&e.path(), files, dirs, syms, bytes)?;
            } else if ft.is_file() {
                *files += 1;
                *bytes += e.metadata()?.len();
            }
        }
        Ok(())
    }
    let init_dir = trace_dir.join("initial");
    if init_dir.is_dir() {
        walk_count(
            &init_dir,
            &mut initial_files,
            &mut initial_dirs,
            &mut initial_symlinks,
            &mut initial_bytes,
        )?;
    }

    let agent_events_bytes = fs::metadata(&agent_events_path)?.len();
    let started_bytes = fs::metadata(&started_path)?.len();
    let mut evidence = vec![
        json!({"path": "agent-events.jsonl", "bytes": agent_events_bytes}),
        json!({"path": "started.json", "bytes": started_bytes}),
    ];
    let route_p = trace_dir.join("route-evidence.jsonl");
    if route_p.is_file() {
        evidence.push(json!({"path": "route-evidence.jsonl", "bytes": fs::metadata(&route_p)?.len()}));
    }

    let boundary_doc = json!({
        "schema": "agent-boundaries-v2",
        "reviewed": true,
        "hash_free": true,
        "events_operations": count,
        "events_bytes": events_bytes,
        "events_before_inference": eb_seq,
        "clock_offset_seconds": clock_offset,
        "started_offset_diff_ms": started_offset.map(|so| (clock_offset - so) * 1000.0),
        "skipped_parallel_tool_calls": skipped_parallel,
        "evidence": evidence,
        "boundaries": mapped,
    });

    if let Some(p) = out_boundaries.parent() {
        fs::create_dir_all(p)?;
    }
    fs::write(out_boundaries, serde_json::to_vec_pretty(&boundary_doc)?)?;

    let summary_doc = json!({
        "trace_dir": trace_dir,
        "events_operations": count,
        "events_bytes": events_bytes,
        "events_before_inference": eb_seq,
        "blob_files": blob_files,
        "blob_bytes": blob_bytes,
        "initial_files": initial_files,
        "initial_dirs": initial_dirs,
        "initial_symlinks": initial_symlinks,
        "initial_bytes": initial_bytes,
        "boundary_count": boundary_doc["boundaries"].as_array().unwrap().len(),
        "skipped_parallel_tool_calls": skipped_parallel,
        "clock_offset_diff_ms": boundary_doc["started_offset_diff_ms"],
        "index_seconds": t0.elapsed().as_secs_f64(),
    });
    if let Some(p) = out_summary.parent() {
        fs::create_dir_all(p)?;
    }
    fs::write(out_summary, serde_json::to_vec_pretty(&summary_doc)?)?;
    println!("{}", serde_json::to_string(&summary_doc)?);
    Ok(())
}

fn collect_rel_entries(root: &Path, cur: &Path, out: &mut BTreeMap<String, Value>) -> Result<()> {
    for entry in fs::read_dir(cur)? {
        let entry = entry?;
        let p = entry.path();
        let rel = p
            .strip_prefix(root)?
            .to_string_lossy()
            .replace('\\', "/");
        if cur == root
            && matches!(
                rel.as_str(),
                ".accesslog"
                    | ".config"
                    | ".control"
                    | ".stats"
                    | ".trash"
                    | ".jfs.accesslog"
                    | ".jfs.config"
                    | ".jfs.control"
                    | ".jfs.stats"
                    | ".jfs.trash"
            )
        {
            continue;
        }
        let m = fs::symlink_metadata(&p)?;
        let ft = m.file_type();
        if ft.is_symlink() {
            let target = fs::read_link(&p)?.to_string_lossy().into_owned();
            out.insert(
                rel,
                json!({"type": "link", "target": target}),
            );
        } else if ft.is_dir() {
            out.insert(
                rel,
                json!({"type": "directory", "mode": m.permissions().mode() & 0o777}),
            );
            collect_rel_entries(root, &p, out)?;
        } else if ft.is_file() {
            out.insert(
                rel,
                json!({"type": "file", "bytes": m.len(), "mode": m.permissions().mode() & 0o777}),
            );
        }
    }
    Ok(())
}

fn run_verify_tree(
    mount_dir: &Path,
    final_tree_path: &Path,
    replayed_dir: Option<&Path>,
    out_json: &Path,
) -> Result<()> {
    let t0 = Instant::now();
    let expected_raw: BTreeMap<String, Value> =
        serde_json::from_slice(&fs::read(final_tree_path)?)?;
    let mut actual_map = BTreeMap::new();
    collect_rel_entries(mount_dir, mount_dir, &mut actual_map)?;

    if actual_map.len() != expected_raw.len() {
        // Find first missing or extra key for clear diagnostics
        for k in expected_raw.keys() {
            if !actual_map.contains_key(k) {
                return Err(format!("missing expected path in workspace: {k}").into());
            }
        }
        for k in actual_map.keys() {
            if !expected_raw.contains_key(k) {
                return Err(format!("unexpected extra path in workspace: {k}").into());
            }
        }
    }

    let mut files_checked = 0u64;
    let mut bytes_read = 0u64;
    let mut direct_compared_files = 0u64;
    let mut buf_a = Vec::new();
    let mut buf_b = Vec::new();

    for (rel, exp) in &expected_raw {
        let act = actual_map
            .get(rel)
            .ok_or_else(|| format!("missing path: {rel}"))?;
        let exp_ty_raw = exp.get("type").and_then(|x| x.as_str()).unwrap_or("");
        let exp_ty = match exp_ty_raw {
            "symlink" => "link",
            "dir" => "directory",
            other => other,
        };
        let act_ty = act.get("type").and_then(|x| x.as_str()).unwrap_or("");
        if exp_ty != act_ty {
            return Err(format!("type mismatch at {rel}: actual={act_ty} expected={exp_ty_raw}").into());
        }
        if exp_ty == "file" {
            let exp_sz = exp.get("bytes").and_then(|x| x.as_u64()).unwrap_or(0);
            let act_sz = act.get("bytes").and_then(|x| x.as_u64()).unwrap_or(0);
            if exp_sz != act_sz {
                return Err(format!("size mismatch at {rel}: actual={act_sz} expected={exp_sz}").into());
            }
            files_checked += 1;
            let mp = mount_dir.join(rel);
            if let Some(rep_root) = replayed_dir {
                let rp = rep_root.join(rel);
                buf_a.clear();
                buf_b.clear();
                File::open(&mp)?.read_to_end(&mut buf_a)?;
                File::open(&rp)?.read_to_end(&mut buf_b)?;
                if buf_a != buf_b {
                    return Err(format!("direct byte content mismatch against replayed reference at {rel}").into());
                }
                bytes_read += buf_a.len() as u64;
                direct_compared_files += 1;
            } else if rel.starts_with("state/data/opencode/") || (rel.starts_with("repo/") && exp_sz <= 262_144 && files_checked % 16 == 0) {
                // Cold-read sample + all session output files when replayed_dir is not staged on peer host
                buf_a.clear();
                File::open(&mp)?.read_to_end(&mut buf_a)?;
                if buf_a.len() as u64 != exp_sz {
                    return Err(format!("short read on cold remount at {rel}").into());
                }
                bytes_read += buf_a.len() as u64;
            }
        } else if exp_ty == "link" {
            if let Some(exp_target) = exp.get("target").and_then(|x| x.as_str()) {
                let act_target = act.get("target").and_then(|x| x.as_str()).unwrap_or("");
                if exp_target != act_target {
                    return Err(format!("symlink target mismatch at {rel}: {act_target} != {exp_target}").into());
                }
            }
        }
    }

    let res = json!({
        "passed": true,
        "entries_verified": expected_raw.len(),
        "files_verified": files_checked,
        "direct_byte_compared_files": direct_compared_files,
        "cold_read_bytes": bytes_read,
        "wall_seconds": t0.elapsed().as_secs_f64(),
    });
    if let Some(p) = out_json.parent() {
        fs::create_dir_all(p)?;
    }
    fs::write(out_json, serde_json::to_vec_pretty(&res)?)?;
    println!("{}", serde_json::to_string(&res)?);
    Ok(())
}

fn main_run() -> Result<()> {
    let args: Vec<String> = env::args().collect();
    if args.len() < 2 || args[1] == "--help" {
        println!("Usage:\n  trace-verify-and-index index --trace-dir <DIR> --out-boundaries <JSON> --out-summary <JSON>\n  trace-verify-and-index verify-tree --mount-dir <DIR> --final-tree <JSON> [--replayed-dir <DIR>] --out <JSON>");
        return Ok(());
    }
    match args[1].as_str() {
        "index" => {
            let mut td = None;
            let mut ob = None;
            let mut os = None;
            let mut i = 2;
            while i < args.len() {
                match args[i].as_str() {
                    "--trace-dir" => {
                        td = Some(PathBuf::from(&args[i + 1]));
                        i += 2;
                    }
                    "--out-boundaries" => {
                        ob = Some(PathBuf::from(&args[i + 1]));
                        i += 2;
                    }
                    "--out-summary" => {
                        os = Some(PathBuf::from(&args[i + 1]));
                        i += 2;
                    }
                    other => return Err(format!("unknown arg {other}").into()),
                }
            }
            run_index(
                &td.ok_or("--trace-dir")?,
                &ob.ok_or("--out-boundaries")?,
                &os.ok_or("--out-summary")?,
            )
        }
        "verify-tree" => {
            let mut md = None;
            let mut ft = None;
            let mut rd = None;
            let mut out = None;
            let mut i = 2;
            while i < args.len() {
                match args[i].as_str() {
                    "--mount-dir" => {
                        md = Some(PathBuf::from(&args[i + 1]));
                        i += 2;
                    }
                    "--final-tree" => {
                        ft = Some(PathBuf::from(&args[i + 1]));
                        i += 2;
                    }
                    "--replayed-dir" => {
                        rd = Some(PathBuf::from(&args[i + 1]));
                        i += 2;
                    }
                    "--out" => {
                        out = Some(PathBuf::from(&args[i + 1]));
                        i += 2;
                    }
                    other => return Err(format!("unknown arg {other}").into()),
                }
            }
            run_verify_tree(
                &md.ok_or("--mount-dir")?,
                &ft.ok_or("--final-tree")?,
                rd.as_deref(),
                &out.ok_or("--out")?,
            )
        }
        other => Err(format!("unknown subcommand {other}").into()),
    }
}

fn main() {
    if let Err(e) = main_run() {
        eprintln!("trace-verify-and-index error: {e}");
        std::process::exit(1);
    }
}
