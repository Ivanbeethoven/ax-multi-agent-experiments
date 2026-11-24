//! Hash-free C1 namespace & small-file benchmark matching experiments/c1-io/README.md.
//! Layout: 1,000 files per directory under <root>/ns/d0000..
//! Segments:
//!   prepare, readdir, getattr, read-4k,
//!   overwrite-1pct, repeat-overwrite-100x10, rename-1pct, delete-1pct, create-1pct,
//!   verify-initial, verify-final.

use std::{
    collections::BTreeSet,
    env,
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    path::{Path, PathBuf},
    thread,
    time::Instant,
};

type Result<T> = std::result::Result<T, Box<dyn std::error::Error + Send + Sync>>;

fn splitmix64(state: &mut u64) -> u64 {
    *state = state.wrapping_add(0x9e3779b97f4a7c15);
    let mut z = *state;
    z = (z ^ (z >> 30)).wrapping_mul(0xbf58476d1ce4e5b9);
    z = (z ^ (z >> 27)).wrapping_mul(0x94d049bb133111eb);
    z ^ (z >> 31)
}

fn payload(index: u64, version: u64) -> [u8; 4096] {
    let mut state = index ^ version.wrapping_mul(0xd6e8feb86659fd93) ^ 0x4331_4e53_5631_u64;
    let mut out = [0u8; 4096];
    for chunk in out.chunks_exact_mut(8) {
        chunk.copy_from_slice(&splitmix64(&mut state).to_le_bytes());
    }
    out
}

fn dir_path(root: &Path, dir_idx: u64) -> PathBuf {
    root.join("ns").join(format!("d{dir_idx:04}"))
}

fn file_path(root: &Path, prefix: char, index: u64) -> PathBuf {
    dir_path(root, index / 1000).join(format!("{prefix}{index:07}"))
}

fn sync_dir(p: &Path) -> Result<()> {
    File::open(p)?.sync_all()?;
    Ok(())
}

struct Hist {
    buckets: [u64; 32],
    samples: Vec<u64>,
}

impl Hist {
    fn new(cap: usize) -> Self {
        Self {
            buckets: [0; 32],
            samples: Vec::with_capacity(cap),
        }
    }
    fn record_ns(&mut self, ns: u64) {
        self.samples.push(ns);
        let us = ns / 1000;
        let b = if us == 0 {
            0
        } else {
            (64 - us.leading_zeros() as usize).min(31)
        };
        self.buckets[b] += 1;
    }
    fn summary_json(&mut self) -> String {
        self.samples.sort_unstable();
        let pct = |p: usize| -> f64 {
            if self.samples.is_empty() {
                0.0
            } else {
                let idx = (self.samples.len() * p).div_ceil(100).saturating_sub(1);
                self.samples[idx] as f64 / 1000.0
            }
        };
        let b_str: Vec<String> = self.buckets.iter().map(|x| x.to_string()).collect();
        format!(
            "{{\"schema\":\"log2-us-v1\",\"count\":{},\"p50_us\":{:.3},\"p95_us\":{:.3},\"p99_us\":{:.3},\"buckets\":[{}]}}",
            self.samples.len(),
            pct(50),
            pct(95),
            pct(99),
            b_str.join(",")
        )
    }
}

fn deterministic_order(files: u64, seed: u64) -> Vec<u64> {
    let mut v: Vec<u64> = (0..files).collect();
    let mut rng = seed ^ 0x71e5_9a2b_4c8d_103f;
    for i in (1..v.len()).rev() {
        let j = (splitmix64(&mut rng) as usize) % (i + 1);
        v.swap(i, j);
    }
    v
}

fn prepare_parallel(root: &Path, files: u64, workers: usize) -> Result<(f64, u64)> {
    let start = Instant::now();
    let ns = root.join("ns");
    fs::create_dir_all(&ns)?;
    let num_dirs = files.div_ceil(1000);
    for d in 0..num_dirs {
        fs::create_dir_all(dir_path(root, d))?;
    }
    let workers = workers.max(1).min(16);
    thread::scope(|s| -> Result<()> {
        let mut handles = Vec::new();
        for w in 0..workers {
            handles.push(s.spawn(move || -> Result<()> {
                for d in (w as u64..num_dirs).step_by(workers) {
                    let first = d * 1000;
                    let last = ((d + 1) * 1000).min(files);
                    for i in first..last {
                        let p = file_path(root, 'f', i);
                        let mut f = OpenOptions::new().write(true).create_new(true).open(&p)?;
                        f.write_all(&payload(i, 0))?;
                    }
                    sync_dir(&dir_path(root, d))?;
                }
                Ok(())
            }));
        }
        for h in handles {
            h.join().map_err(|_| "prepare worker panicked")??;
        }
        Ok(())
    })?;
    sync_dir(&ns)?;
    sync_dir(root)?;
    Ok((start.elapsed().as_secs_f64(), files * 4096))
}

fn verify_tree(root: &Path, files: u64, final_state: bool, workers: usize) -> Result<(f64, u64)> {
    let start = Instant::now();
    let k = files / 100;
    let total_dirs = if final_state {
        (files + k).div_ceil(1000)
    } else {
        files.div_ceil(1000)
    };
    let ns = root.join("ns");
    let found_dirs: BTreeSet<String> = fs::read_dir(&ns)?
        .map(|e| Ok(e?.file_name().to_string_lossy().into_owned()))
        .collect::<std::io::Result<_>>()?;
    let exp_dirs: BTreeSet<String> = (0..total_dirs).map(|d| format!("d{d:04}")).collect();
    if found_dirs != exp_dirs {
        return Err(format!("directory set mismatch: got {} expected {}", found_dirs.len(), exp_dirs.len()).into());
    }

    let workers = workers.max(1).min(16);
    let checked_bytes = thread::scope(|s| -> Result<u64> {
        let mut handles = Vec::new();
        for w in 0..workers {
            handles.push(s.spawn(move || -> Result<u64> {
                let mut bytes = 0u64;
                let mut buf = Vec::with_capacity(4097);
                for d in (w as u64..total_dirs).step_by(workers) {
                    let dp = dir_path(root, d);
                    let mut exp_names = BTreeSet::new();
                    let lo = d * 1000;
                    let hi = ((d + 1) * 1000).min(if final_state { files + k } else { files });
                    for i in lo..hi {
                        if !final_state {
                            exp_names.insert(('f', i, 0u64));
                        } else if i < 100.min(k) {
                            exp_names.insert(('f', i, 11));
                        } else if i < k {
                            exp_names.insert(('f', i, 1));
                        } else if i < 2 * k {
                            exp_names.insert(('r', i, 0));
                        } else if i < 3 * k {
                            // deleted
                        } else if i < files {
                            exp_names.insert(('f', i, 0));
                        } else {
                            exp_names.insert(('n', i, 1));
                        }
                    }
                    let actual_names: BTreeSet<String> = fs::read_dir(&dp)?
                        .map(|e| Ok(e?.file_name().to_string_lossy().into_owned()))
                        .collect::<std::io::Result<_>>()?;
                    let exp_str: BTreeSet<String> = exp_names
                        .iter()
                        .map(|(pfx, idx, _)| format!("{pfx}{idx:07}"))
                        .collect();
                    if actual_names != exp_str {
                        return Err(format!("dir d{d:04} entry mismatch: actual={} expected={}", actual_names.len(), exp_str.len()).into());
                    }
                    for (pfx, idx, ver) in exp_names {
                        let fp = dp.join(format!("{pfx}{idx:07}"));
                        let m = fs::symlink_metadata(&fp)?;
                        if !m.is_file() || m.len() != 4096 {
                            return Err(format!("invalid metadata at {}", fp.display()).into());
                        }
                        buf.clear();
                        File::open(&fp)?.take(4097).read_to_end(&mut buf)?;
                        if buf.as_slice() != payload(idx, ver) {
                            return Err(format!("payload mismatch at {} expected v{ver}", fp.display()).into());
                        }
                        bytes += 4096;
                    }
                }
                Ok(bytes)
            }));
        }
        let mut sum = 0u64;
        for h in handles {
            sum += h.join().map_err(|_| "verify worker panicked")??;
        }
        Ok(sum)
    })?;
    Ok((start.elapsed().as_secs_f64(), checked_bytes))
}

fn main_run() -> Result<()> {
    let args: Vec<String> = env::args().collect();
    if args.len() < 2 || args[1] == "--help" {
        println!("Usage: c1-namespace <segment> --root <DIR> --files <10000|100000> [--seed <U64>] [--workers <N>] [--out <JSON>]");
        return Ok(());
    }
    let seg = args[1].as_str();
    let mut root: Option<PathBuf> = None;
    let mut files: u64 = 0;
    let mut seed: u64 = 2026111401;
    let mut workers: usize = 8;
    let mut out_path: Option<PathBuf> = None;
    let mut i = 2;
    while i < args.len() {
        match args[i].as_str() {
            "--root" => {
                root = Some(PathBuf::from(&args[i + 1]));
                i += 2;
            }
            "--files" => {
                files = args[i + 1].parse()?;
                i += 2;
            }
            "--seed" => {
                seed = args[i + 1].parse()?;
                i += 2;
            }
            "--workers" => {
                workers = args[i + 1].parse()?;
                i += 2;
            }
            "--out" => {
                out_path = Some(PathBuf::from(&args[i + 1]));
                i += 2;
            }
            other => return Err(format!("unknown arg: {other}").into()),
        }
    }
    let root = root.ok_or("--root required")?;
    if files < 1000 || !files.is_multiple_of(1000) {
        return Err("--files must be a positive multiple of 1000".into());
    }
    let k = files / 100;
    let num_dirs = files / 1000;

    let json_out = match seg {
        "prepare" => {
            let (secs, written) = prepare_parallel(&root, files, workers)?;
            format!(
                "{{\"passed\":true,\"segment\":\"prepare\",\"files\":{files},\"operations\":{files},\"read_bytes\":0,\"write_bytes\":{written},\"wall_seconds\":{secs:.6}}}"
            )
        }
        "readdir" => {
            let mut hist = Hist::new((num_dirs + 1) as usize);
            let mut entries = 0u64;
            let start = Instant::now();
            let t0 = Instant::now();
            for e in fs::read_dir(root.join("ns"))? {
                let _ = e?;
            }
            hist.record_ns(t0.elapsed().as_nanos() as u64);
            for d in 0..num_dirs {
                let t = Instant::now();
                for e in fs::read_dir(dir_path(&root, d))? {
                    let _ = e?;
                    entries += 1;
                }
                hist.record_ns(t.elapsed().as_nanos() as u64);
            }
            let secs = start.elapsed().as_secs_f64();
            if entries != files {
                return Err(format!("readdir count {entries} != {files}").into());
            }
            format!(
                "{{\"passed\":true,\"segment\":\"readdir\",\"files\":{files},\"operations\":{},\"directory_entries\":{entries},\"read_bytes\":0,\"write_bytes\":0,\"wall_seconds\":{secs:.6},\"ops_per_sec\":{:.3},\"latency\":{}}}",
                num_dirs + 1,
                (num_dirs + 1) as f64 / secs.max(1e-9),
                hist.summary_json()
            )
        }
        "getattr" => {
            let order = deterministic_order(files, seed ^ 0x1111);
            let mut hist = Hist::new(files as usize);
            let start = Instant::now();
            for idx in order {
                let fp = file_path(&root, 'f', idx);
                let t = Instant::now();
                let m = fs::symlink_metadata(&fp)?;
                hist.record_ns(t.elapsed().as_nanos() as u64);
                if !m.is_file() || m.len() != 4096 {
                    return Err(format!("getattr mismatch at {idx}").into());
                }
            }
            let secs = start.elapsed().as_secs_f64();
            format!(
                "{{\"passed\":true,\"segment\":\"getattr\",\"files\":{files},\"operations\":{files},\"read_bytes\":0,\"write_bytes\":0,\"wall_seconds\":{secs:.6},\"ops_per_sec\":{:.3},\"latency\":{}}}",
                files as f64 / secs.max(1e-9),
                hist.summary_json()
            )
        }
        "read-4k" => {
            let order = deterministic_order(files, seed ^ 0x2222);
            let mut hist = Hist::new(files as usize);
            let mut buf = [0u8; 4096];
            let start = Instant::now();
            for idx in order {
                let fp = file_path(&root, 'f', idx);
                let t = Instant::now();
                let mut f = File::open(&fp)?;
                f.read_exact(&mut buf)?;
                drop(f);
                hist.record_ns(t.elapsed().as_nanos() as u64);
                if buf != payload(idx, 0) {
                    return Err(format!("read-4k content mismatch at {idx}").into());
                }
            }
            let secs = start.elapsed().as_secs_f64();
            let rb = files * 4096;
            format!(
                "{{\"passed\":true,\"segment\":\"read-4k\",\"files\":{files},\"operations\":{files},\"read_bytes\":{rb},\"write_bytes\":0,\"wall_seconds\":{secs:.6},\"ops_per_sec\":{:.3},\"latency\":{}}}",
                files as f64 / secs.max(1e-9),
                hist.summary_json()
            )
        }
        "overwrite-1pct" => {
            let mut hist = Hist::new(k as usize);
            let mut touched_dirs = BTreeSet::new();
            let start = Instant::now();
            for idx in 0..k {
                let fp = file_path(&root, 'f', idx);
                touched_dirs.insert(idx / 1000);
                let data = payload(idx, 1);
                let t = Instant::now();
                let mut f = OpenOptions::new().write(true).truncate(true).open(&fp)?;
                f.write_all(&data)?;
                f.sync_all()?;
                drop(f);
                hist.record_ns(t.elapsed().as_nanos() as u64);
            }
            for d in touched_dirs {
                sync_dir(&dir_path(&root, d))?;
            }
            let secs = start.elapsed().as_secs_f64();
            let wb = k * 4096;
            format!(
                "{{\"passed\":true,\"segment\":\"overwrite-1pct\",\"files\":{files},\"operations\":{k},\"read_bytes\":0,\"write_bytes\":{wb},\"wall_seconds\":{secs:.6},\"ops_per_sec\":{:.3},\"latency\":{}}}",
                k as f64 / secs.max(1e-9),
                hist.summary_json()
            )
        }
        "repeat-overwrite-100x10" => {
            let n_rep = 100u64.min(k);
            let mut hist = Hist::new((n_rep * 10) as usize);
            let start = Instant::now();
            for round in 1..=10u64 {
                for idx in 0..n_rep {
                    let fp = file_path(&root, 'f', idx);
                    let data = payload(idx, 1 + round);
                    let t = Instant::now();
                    let mut f = OpenOptions::new().write(true).truncate(true).open(&fp)?;
                    f.write_all(&data)?;
                    f.sync_all()?;
                    drop(f);
                    hist.record_ns(t.elapsed().as_nanos() as u64);
                }
            }
            sync_dir(&dir_path(&root, 0))?;
            let secs = start.elapsed().as_secs_f64();
            let ops = n_rep * 10;
            let wb = ops * 4096;
            format!(
                "{{\"passed\":true,\"segment\":\"repeat-overwrite-100x10\",\"files\":{files},\"operations\":{ops},\"read_bytes\":0,\"write_bytes\":{wb},\"wall_seconds\":{secs:.6},\"ops_per_sec\":{:.3},\"latency\":{}}}",
                ops as f64 / secs.max(1e-9),
                hist.summary_json()
            )
        }
        "rename-1pct" => {
            let mut hist = Hist::new(k as usize);
            let mut touched_dirs = BTreeSet::new();
            let start = Instant::now();
            for idx in k..(2 * k) {
                let src = file_path(&root, 'f', idx);
                let dst = file_path(&root, 'r', idx);
                touched_dirs.insert(idx / 1000);
                let t = Instant::now();
                fs::rename(&src, &dst)?;
                hist.record_ns(t.elapsed().as_nanos() as u64);
            }
            for d in touched_dirs {
                sync_dir(&dir_path(&root, d))?;
            }
            let secs = start.elapsed().as_secs_f64();
            format!(
                "{{\"passed\":true,\"segment\":\"rename-1pct\",\"files\":{files},\"operations\":{k},\"read_bytes\":0,\"write_bytes\":0,\"wall_seconds\":{secs:.6},\"ops_per_sec\":{:.3},\"latency\":{}}}",
                k as f64 / secs.max(1e-9),
                hist.summary_json()
            )
        }
        "delete-1pct" => {
            let mut hist = Hist::new(k as usize);
            let mut touched_dirs = BTreeSet::new();
            let start = Instant::now();
            for idx in (2 * k)..(3 * k) {
                let fp = file_path(&root, 'f', idx);
                touched_dirs.insert(idx / 1000);
                let t = Instant::now();
                fs::remove_file(&fp)?;
                hist.record_ns(t.elapsed().as_nanos() as u64);
            }
            for d in touched_dirs {
                sync_dir(&dir_path(&root, d))?;
            }
            let secs = start.elapsed().as_secs_f64();
            format!(
                "{{\"passed\":true,\"segment\":\"delete-1pct\",\"files\":{files},\"operations\":{k},\"read_bytes\":0,\"write_bytes\":0,\"wall_seconds\":{secs:.6},\"ops_per_sec\":{:.3},\"latency\":{}}}",
                k as f64 / secs.max(1e-9),
                hist.summary_json()
            )
        }
        "create-1pct" => {
            let mut hist = Hist::new(k as usize);
            let mut touched_dirs = BTreeSet::new();
            let start = Instant::now();
            for idx in files..(files + k) {
                let d = idx / 1000;
                if touched_dirs.insert(d) {
                    fs::create_dir_all(dir_path(&root, d))?;
                }
                let fp = file_path(&root, 'n', idx);
                let data = payload(idx, 1);
                let t = Instant::now();
                let mut f = OpenOptions::new().write(true).create_new(true).open(&fp)?;
                f.write_all(&data)?;
                f.sync_all()?;
                drop(f);
                hist.record_ns(t.elapsed().as_nanos() as u64);
            }
            for d in touched_dirs {
                sync_dir(&dir_path(&root, d))?;
            }
            sync_dir(&root.join("ns"))?;
            let secs = start.elapsed().as_secs_f64();
            let wb = k * 4096;
            format!(
                "{{\"passed\":true,\"segment\":\"create-1pct\",\"files\":{files},\"operations\":{k},\"read_bytes\":0,\"write_bytes\":{wb},\"wall_seconds\":{secs:.6},\"ops_per_sec\":{:.3},\"latency\":{}}}",
                k as f64 / secs.max(1e-9),
                hist.summary_json()
            )
        }
        "verify-initial" | "verify-final" => {
            let (secs, bytes) = verify_tree(&root, files, seg == "verify-final", workers)?;
            format!(
                "{{\"passed\":true,\"segment\":\"{seg}\",\"files\":{files},\"bytes_checked\":{bytes},\"wall_seconds\":{secs:.6}}}"
            )
        }
        other => return Err(format!("unknown segment: {other}").into()),
    };

    if let Some(p) = out_path {
        if let Some(parent) = p.parent() {
            fs::create_dir_all(parent)?;
        }
        fs::write(&p, format!("{json_out}\n"))?;
    }
    println!("{json_out}");
    Ok(())
}

fn main() {
    if let Err(e) = main_run() {
        eprintln!("c1-namespace error: {e}");
        std::process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn lifecycle_and_verification_1000_files() {
        let tmp = env::temp_dir().join(format!("c1-ns-test-{}", std::process::id()));
        let _ = fs::remove_dir_all(&tmp);
        fs::create_dir_all(&tmp).unwrap();
        prepare_parallel(&tmp, 1000, 2).unwrap();
        verify_tree(&tmp, 1000, false, 2).unwrap();
        let _ = fs::remove_dir_all(&tmp);
    }
}
