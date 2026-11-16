//! Hash-free C3 shared dataset loader & fork-child workload.
//! Implements:
//! - `c3-loader load`: 2-thread (configurable), batch=64 shuffled multi-epoch dataset reader.
//! - `c3-loader fork-child`: post-fork cold first-read + 1% private modification + touched-state writer.
use std::{
    collections::{BTreeMap, BTreeSet},
    env,
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    path::{Path, PathBuf},
    sync::atomic::{AtomicUsize, Ordering},
    thread,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

type Result<T> = std::result::Result<T, Box<dyn std::error::Error + Send + Sync>>;

const LOG2_US_BUCKETS: usize = 32;

#[derive(Clone)]
struct Log2UsHist {
    counts: [u64; LOG2_US_BUCKETS],
    count: u64,
    sum_ns: u128,
    min_ns: u64,
    max_ns: u64,
}

impl Default for Log2UsHist {
    fn default() -> Self {
        Self {
            counts: [0; LOG2_US_BUCKETS],
            count: 0,
            sum_ns: 0,
            min_ns: u64::MAX,
            max_ns: 0,
        }
    }
}

impl Log2UsHist {
    fn record_ns(&mut self, ns: u64) {
        let us = (ns / 1000).max(1);
        let bucket = ((63 - us.leading_zeros()) as usize).min(LOG2_US_BUCKETS - 1);
        self.counts[bucket] += 1;
        self.count += 1;
        self.sum_ns += ns as u128;
        self.min_ns = self.min_ns.min(ns);
        self.max_ns = self.max_ns.max(ns);
    }

    fn merge(&mut self, other: &Self) {
        for (a, b) in self.counts.iter_mut().zip(other.counts.iter()) {
            *a += *b;
        }
        self.count += other.count;
        self.sum_ns += other.sum_ns;
        self.min_ns = self.min_ns.min(other.min_ns);
        self.max_ns = self.max_ns.max(other.max_ns);
    }

    fn upper_bound_quantile_us(&self, q: f64) -> f64 {
        if self.count == 0 {
            return 0.0;
        }
        let target = ((self.count as f64) * q).ceil().max(1.0) as u64;
        let mut acc = 0u64;
        for (i, c) in self.counts.iter().enumerate() {
            acc += *c;
            if acc >= target {
                if i == LOG2_US_BUCKETS - 1 {
                    return (self.max_ns as f64) / 1000.0;
                }
                return (1u64 << (i + 1)) as f64;
            }
        }
        (self.max_ns as f64) / 1000.0
    }

    fn to_json(&self) -> String {
        let buckets = self
            .counts
            .iter()
            .map(|x| x.to_string())
            .collect::<Vec<_>>()
            .join(",");
        let min_us = if self.count == 0 {
            0.0
        } else {
            (self.min_ns as f64) / 1000.0
        };
        let max_us = (self.max_ns as f64) / 1000.0;
        format!(
            "{{\"schema\":\"log2-us-v1\",\"count\":{},\"sum_ns\":{},\"min_us\":{min_us:.3},\"max_us\":{max_us:.3},\"p50_us\":{:.3},\"p95_us\":{:.3},\"p99_us\":{:.3},\"buckets\":[{buckets}]}}",
            self.count,
            self.sum_ns,
            self.upper_bound_quantile_us(0.50),
            self.upper_bound_quantile_us(0.95),
            self.upper_bound_quantile_us(0.99),
        )
    }
}

fn next(s: &mut u64) -> u64 {
    *s = s.wrapping_add(0x9e3779b97f4a7c15);
    let mut z = *s;
    z = (z ^ (z >> 30)).wrapping_mul(0xbf58476d1ce4e5b9);
    z = (z ^ (z >> 27)).wrapping_mul(0x94d049bb133111eb);
    z ^ (z >> 31)
}

fn bounded(s: &mut u64, n: u64) -> u64 {
    let threshold = n.wrapping_neg() % n;
    loop {
        let x = next(s);
        if x >= threshold {
            return x % n;
        }
    }
}

fn file_path(root: &Path, index: u64) -> PathBuf {
    root.join("small")
        .join(format!("d{:04}", index / 1000))
        .join(format!("f{index:07}"))
}

fn payload(index: u64, version: u64, workspace: u64) -> [u8; 4096] {
    let mut state = index
        ^ version.wrapping_mul(0xd6e8feb86659fd93)
        ^ if version == 0 {
            0
        } else {
            workspace.rotate_left(17)
        };
    let mut bytes = [0u8; 4096];
    for part in bytes.chunks_exact_mut(8) {
        part.copy_from_slice(&next(&mut state).to_le_bytes());
    }
    bytes
}

fn shuffled_indices(files: u64, seed: u64, epoch: u64) -> Vec<u64> {
    let mut rng = seed ^ epoch.wrapping_mul(0x517cc1b727220a95) ^ 0xa0761d6478bd642f;
    let mut v: Vec<u64> = (0..files).collect();
    for i in (1..v.len()).rev() {
        let j = bounded(&mut rng, (i + 1) as u64) as usize;
        v.swap(i, j);
    }
    v
}

fn wait_for_start_barrier(opts: &BTreeMap<&str, &str>) -> Result<()> {
    let raw = if let Some(v) = opts.get("--start-unix-ns") {
        Some((*v).to_string())
    } else {
        env::var("AX_START_UNIX_NS").ok()
    };
    if let Some(val) = raw {
        let target: u128 = val.parse()?;
        let now = SystemTime::now().duration_since(UNIX_EPOCH)?.as_nanos();
        if target > now {
            thread::sleep(Duration::from_nanos((target - now).try_into()?));
        }
    }
    Ok(())
}

fn parse_opts<'a>(args: &'a [String], allowed: &[&str]) -> Result<BTreeMap<&'a str, &'a str>> {
    if !args.len().is_multiple_of(2) {
        return Err("expected --flag value pairs".into());
    }
    let mut map = BTreeMap::new();
    for pair in args.chunks_exact(2) {
        let k = pair[0].as_str();
        let v = pair[1].as_str();
        if !allowed.contains(&k) || map.insert(k, v).is_some() {
            return Err(format!("unknown or duplicate option: {k}").into());
        }
    }
    Ok(map)
}

fn write_atomic_new(path: &Path, text: &str) -> Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let mut f = OpenOptions::new().write(true).create_new(true).open(path)?;
    f.write_all(text.as_bytes())?;
    f.sync_all()?;
    Ok(())
}

fn run_load(args: &[String]) -> Result<()> {
    let opts = parse_opts(
        args,
        &[
            "--root",
            "--output",
            "--files",
            "--epochs",
            "--threads",
            "--batch",
            "--seed",
            "--verify-bytes",
            "--start-unix-ns",
        ],
    )?;
    let root = PathBuf::from(*opts.get("--root").ok_or("--root required")?);
    let output = PathBuf::from(*opts.get("--output").ok_or("--output required")?);
    let files: u64 = opts.get("--files").unwrap_or(&"100000").parse()?;
    let epochs: u64 = opts.get("--epochs").unwrap_or(&"3").parse()?;
    let threads: usize = opts.get("--threads").unwrap_or(&"2").parse()?;
    let batch_size: usize = opts.get("--batch").unwrap_or(&"64").parse()?;
    let seed: u64 = opts.get("--seed").unwrap_or(&"1001").parse()?;
    let verify_bytes: bool = opts.get("--verify-bytes").unwrap_or(&"1") != &"0";
    if files == 0 || epochs == 0 || threads == 0 || threads > 32 || batch_size == 0 {
        return Err("positive files/epochs/threads/batch required".into());
    }
    if output.exists() || output.starts_with(&root) {
        return Err("output must be new and outside root".into());
    }

    // Pre-generate deterministic shuffle orders before the timed barrier so CPU shuffle never skews I/O timing.
    let schedules: Vec<Vec<u64>> = (0..epochs)
        .map(|ep| shuffled_indices(files, seed, ep))
        .collect();

    wait_for_start_barrier(&opts)?;
    let total_start = Instant::now();
    let mut total_hist = Log2UsHist::default();
    let mut epoch_jsons = Vec::with_capacity(epochs as usize);
    let mut first_batch_64_seconds: Option<f64> = None;
    let mut total_read_bytes = 0u64;
    let mut total_ops = 0u64;
    let mut total_fs_ns = 0u128;

    for (ep_idx, order) in schedules.iter().enumerate() {
        let ep_start = Instant::now();
        let next_batch = AtomicUsize::new(0);
        let num_batches = order.len().div_ceil(batch_size);
        let first_batch_target = files.min(batch_size as u64) as usize;
        let completed_files = AtomicUsize::new(0);
        let first_batch_ns = AtomicUsize::new(0);

        let worker_results = thread::scope(|scope| -> Result<Vec<(Log2UsHist, u64, u128)>> {
            let mut handles = Vec::with_capacity(threads);
            for _ in 0..threads {
                let root = &root;
                let order = order.as_slice();
                let next_batch = &next_batch;
                let completed_files = &completed_files;
                let first_batch_ns = &first_batch_ns;
                handles.push(scope.spawn(move || -> Result<(Log2UsHist, u64, u128)> {
                    let mut hist = Log2UsHist::default();
                    let mut read_bytes = 0u64;
                    let mut fs_ns = 0u128;
                    let mut buf = [0u8; 4096];
                    loop {
                        let b = next_batch.fetch_add(1, Ordering::Relaxed);
                        if b >= num_batches {
                            break;
                        }
                        let start_idx = b * batch_size;
                        let end_idx = (start_idx + batch_size).min(order.len());
                        for &file_idx in &order[start_idx..end_idx] {
                            let op_t0 = Instant::now();
                            let p = file_path(root, file_idx);
                            let mut f = File::open(&p).map_err(|error| {
                                format!("open {} epoch {ep_idx} batch {b}: {error}", p.display())
                            })?;
                            f.read_exact(&mut buf).map_err(|error| {
                                format!("read {} epoch {ep_idx} batch {b}: {error}", p.display())
                            })?;
                            drop(f);
                            let op_ns = op_t0.elapsed().as_nanos() as u64;
                            if verify_bytes && buf != payload(file_idx, 0, 0) {
                                return Err(
                                    format!("payload mismatch at file {file_idx} epoch {ep_idx}")
                                        .into(),
                                );
                            }
                            hist.record_ns(op_ns);
                            fs_ns += op_ns as u128;
                            read_bytes += 4096;
                            let done = completed_files.fetch_add(1, Ordering::AcqRel) + 1;
                            if done == first_batch_target {
                                let elapsed_ns = ep_start.elapsed().as_nanos() as usize;
                                let _ = first_batch_ns.compare_exchange(
                                    0,
                                    elapsed_ns.max(1),
                                    Ordering::SeqCst,
                                    Ordering::SeqCst,
                                );
                            }
                        }
                    }
                    Ok((hist, read_bytes, fs_ns))
                }));
            }
            let mut out = Vec::with_capacity(threads);
            for h in handles {
                out.push(h.join().map_err(|_| "reader worker panicked")??);
            }
            Ok(out)
        })?;

        let ep_wall = ep_start.elapsed().as_secs_f64();
        let mut ep_hist = Log2UsHist::default();
        let mut ep_bytes = 0u64;
        let mut ep_fs_ns = 0u128;
        for (h, b, ns) in worker_results {
            ep_hist.merge(&h);
            ep_bytes += b;
            ep_fs_ns += ns;
        }
        let fb_sec = {
            let ns = first_batch_ns.load(Ordering::SeqCst);
            if ns == 0 {
                ep_wall
            } else {
                (ns as f64) / 1e9
            }
        };
        if ep_idx == 0 {
            first_batch_64_seconds = Some(fb_sec);
        }
        total_hist.merge(&ep_hist);
        total_read_bytes += ep_bytes;
        total_ops += files;
        total_fs_ns += ep_fs_ns;
        epoch_jsons.push(format!(
            "{{\"epoch\":{ep_idx},\"files\":{files},\"read_bytes\":{ep_bytes},\"wall_seconds\":{ep_wall:.6},\"first_batch_64_seconds\":{fb_sec:.6},\"fs_operation_seconds\":{:.6},\"latency\":{}}}",
            (ep_fs_ns as f64) / 1e9,
            ep_hist.to_json()
        ));
    }

    let wall_seconds = total_start.elapsed().as_secs_f64();
    let unique_read_bytes = files * 4096;
    let repeated_read_ratio = if total_read_bytes == 0 {
        0.0
    } else {
        1.0 - (unique_read_bytes as f64) / (total_read_bytes as f64)
    };
    let hist_json = total_hist.to_json();
    let json = format!(
        "{{\"completed\":true,\"passed\":true,\"mode\":\"load\",\"files\":{files},\"epochs\":{epochs},\"threads\":{threads},\"batch_size\":{batch_size},\"seed\":{seed},\"verify_bytes\":{verify_bytes},\"operations\":{total_ops},\"read_ops\":{total_ops},\"write_ops\":0,\"actual_read_bytes\":{total_read_bytes},\"actual_write_bytes\":0,\"unique_read_bytes\":{unique_read_bytes},\"repeated_read_ratio\":{repeated_read_ratio:.6},\"first_batch_64_seconds\":{:.6},\"wall_seconds\":{wall_seconds:.6},\"fs_operation_seconds\":{:.6},\"epoch_results\":[{}],\"histograms\":{{\"all\":{hist_json},\"read\":{hist_json}}}}}\n",
        first_batch_64_seconds.unwrap_or(wall_seconds),
        (total_fs_ns as f64) / 1e9,
        epoch_jsons.join(",")
    );
    write_atomic_new(&output, &json)?;
    print!("{json}");
    Ok(())
}

fn select_dirty_indices(files: u64, dirty_count: usize, seed: u64, workspace: u64) -> Vec<u64> {
    let mut rng = seed ^ workspace.wrapping_mul(0x9e3779b97f4a7c15) ^ 0x243f6a8885a308d3;
    let mut seen = BTreeSet::new();
    while seen.len() < dirty_count {
        seen.insert(bounded(&mut rng, files));
    }
    seen.into_iter().collect()
}

fn run_fork_child(args: &[String]) -> Result<()> {
    let opts = parse_opts(
        args,
        &[
            "--root",
            "--output",
            "--state-out",
            "--files",
            "--dirty-count",
            "--seed",
            "--workspace",
            "--start-unix-ns",
        ],
    )?;
    let root = PathBuf::from(*opts.get("--root").ok_or("--root required")?);
    let output = PathBuf::from(*opts.get("--output").ok_or("--output required")?);
    let state_out = PathBuf::from(*opts.get("--state-out").ok_or("--state-out required")?);
    let files: u64 = opts.get("--files").unwrap_or(&"10000").parse()?;
    let default_dirty = ((files / 100).max(1)).to_string();
    let dirty_count: usize = opts
        .get("--dirty-count")
        .unwrap_or(&default_dirty.as_str())
        .parse()?;
    let seed: u64 = opts.get("--seed").unwrap_or(&"2026").parse()?;
    let workspace: u64 = opts.get("--workspace").ok_or("--workspace required")?.parse()?;
    if files == 0 || dirty_count == 0 || dirty_count > files as usize || workspace == 0 {
        return Err("valid files/dirty-count/workspace required".into());
    }
    if output.exists() || state_out.exists() || output.starts_with(&root) || state_out.starts_with(&root) {
        return Err("output and state-out must be new and outside root".into());
    }

    let dirty_indices = select_dirty_indices(files, dirty_count, seed, workspace);
    // Also include a deterministic 64-file initial read batch to measure first_read_seconds cleanly.
    let first_batch_indices: Vec<u64> = {
        let mut s = seed ^ workspace.rotate_left(13);
        (0..files.min(64)).map(|_| bounded(&mut s, files)).collect()
    };

    wait_for_start_barrier(&opts)?;
    let total_start = Instant::now();
    let mut all_hist = Log2UsHist::default();
    let mut read_hist = Log2UsHist::default();
    let mut write_hist = Log2UsHist::default();
    let mut buf = [0u8; 4096];
    let mut read_bytes = 0u64;
    let mut write_bytes = 0u64;

    // 1. First read phase: read first 64 files + all dirty target files from inherited parent (version=0).
    let read_start = Instant::now();
    let mut first_file_seconds = 0.0f64;
    for (k, &idx) in first_batch_indices.iter().enumerate() {
        let t0 = Instant::now();
        let mut f = File::open(file_path(&root, idx))?;
        f.read_exact(&mut buf)?;
        drop(f);
        let ns = t0.elapsed().as_nanos() as u64;
        if k == 0 {
            first_file_seconds = (ns as f64) / 1e9;
        }
        if buf != payload(idx, 0, 0) {
            return Err(format!("inherited base mismatch on first read at file {idx}").into());
        }
        all_hist.record_ns(ns);
        read_hist.record_ns(ns);
        read_bytes += 4096;
    }
    let first_batch_64_seconds = read_start.elapsed().as_secs_f64();

    for &idx in &dirty_indices {
        let t0 = Instant::now();
        let mut f = File::open(file_path(&root, idx))?;
        f.read_exact(&mut buf)?;
        drop(f);
        let ns = t0.elapsed().as_nanos() as u64;
        if buf != payload(idx, 0, 0) {
            return Err(format!("inherited base mismatch before write at file {idx}").into());
        }
        all_hist.record_ns(ns);
        read_hist.record_ns(ns);
        read_bytes += 4096;
    }
    let first_read_seconds = read_start.elapsed().as_secs_f64();

    // 2. Private 1% modification phase: overwrite dirty_indices with version=1 for this workspace.
    let write_start = Instant::now();
    let mut touched_dirs = BTreeSet::new();
    for &idx in &dirty_indices {
        let expected = payload(idx, 1, workspace);
        let p = file_path(&root, idx);
        if let Some(parent) = p.parent() {
            touched_dirs.insert(parent.to_path_buf());
        }
        let t0 = Instant::now();
        let mut f = OpenOptions::new().write(true).open(&p)?;
        f.write_all(&expected)?;
        f.sync_all()?;
        drop(f);
        let ns = t0.elapsed().as_nanos() as u64;
        all_hist.record_ns(ns);
        write_hist.record_ns(ns);
        write_bytes += 4096;
    }
    for dir in &touched_dirs {
        File::open(dir)?.sync_all()?;
    }
    File::open(root.join("small"))?.sync_all()?;
    File::open(&root)?.sync_all()?;
    let private_write_seconds = write_start.elapsed().as_secs_f64();

    // 3. Immediate readback check on touched files inside this child mount.
    for &idx in &dirty_indices {
        let mut f = File::open(file_path(&root, idx))?;
        f.read_exact(&mut buf)?;
        if buf != payload(idx, 1, workspace) {
            return Err(format!("private write readback mismatch at file {idx}").into());
        }
    }

    let mut state_text = format!("smallfiles-v1\t{files}\t{workspace}\n");
    for &idx in &dirty_indices {
        state_text.push_str(&format!("{idx}\t1\n"));
    }
    write_atomic_new(&state_out, &state_text)?;

    let wall_seconds = total_start.elapsed().as_secs_f64();
    let read_ops = (first_batch_indices.len() + dirty_indices.len()) as u64;
    let write_ops = dirty_indices.len() as u64;
    let json = format!(
        "{{\"completed\":true,\"passed\":true,\"mode\":\"fork-child\",\"files\":{files},\"workspace\":{workspace},\"dirty_files\":{},\"seed\":{seed},\"operations\":{},\"read_ops\":{read_ops},\"write_ops\":{write_ops},\"actual_read_bytes\":{read_bytes},\"actual_write_bytes\":{write_bytes},\"first_file_seconds\":{first_file_seconds:.6},\"first_batch_64_seconds\":{first_batch_64_seconds:.6},\"first_read_seconds\":{first_read_seconds:.6},\"private_write_seconds\":{private_write_seconds:.6},\"wall_seconds\":{wall_seconds:.6},\"histograms\":{{\"all\":{},\"read\":{},\"write\":{}}}}}\n",
        dirty_indices.len(),
        read_ops + write_ops,
        all_hist.to_json(),
        read_hist.to_json(),
        write_hist.to_json(),
    );
    write_atomic_new(&output, &json)?;
    print!("{json}");
    Ok(())
}

fn main() {
    let args: Vec<String> = env::args().collect();
    let res = match args.get(1).map(|s| s.as_str()) {
        Some("load") => run_load(&args[2..]),
        Some("fork-child") => run_fork_child(&args[2..]),
        _ => Err(
            "usage: c3-loader <load|fork-child> --root DIR --output FILE [options]".into(),
        ),
    };
    if let Err(e) = res {
        eprintln!("{e}");
        std::process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn shuffle_is_bijection_and_deterministic() {
        let a = shuffled_indices(1000, 42, 0);
        let b = shuffled_indices(1000, 42, 0);
        let c = shuffled_indices(1000, 42, 1);
        assert_eq!(a, b);
        assert_ne!(a, c);
        let uniq: BTreeSet<u64> = a.into_iter().collect();
        assert_eq!(uniq.len(), 1000);
    }
    #[test]
    fn dirty_selection_is_workspace_specific() {
        let w1 = select_dirty_indices(10000, 100, 99, 1);
        let w2 = select_dirty_indices(10000, 100, 99, 2);
        assert_eq!(w1.len(), 100);
        assert_eq!(w2.len(), 100);
        assert_ne!(w1, w2);
    }
}
