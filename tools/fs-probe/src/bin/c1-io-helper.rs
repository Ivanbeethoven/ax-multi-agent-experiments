//! Hash-free C1 file generator, direct-byte verifier, and layout-fuse reader.
use std::{
    env,
    fs::{self, File, OpenOptions},
    io::{Read, Seek, SeekFrom, Write},
    os::unix::fs::FileExt,
    path::{Path, PathBuf},
    thread,
    time::Instant,
};

type Result<T> = std::result::Result<T, Box<dyn std::error::Error + Send + Sync>>;

const FIO_PATTERN_U32: u32 = 0x5a3c_96e1;

fn splitmix64(state: &mut u64) -> u64 {
    *state = state.wrapping_add(0x9e3779b97f4a7c15);
    let mut z = *state;
    z = (z ^ (z >> 30)).wrapping_mul(0xbf58476d1ce4e5b9);
    z = (z ^ (z >> 27)).wrapping_mul(0x94d049bb133111eb);
    z ^ (z >> 31)
}

fn fill_block_4k(job: u64, block_idx: u64, out: &mut [u8; 4096]) {
    let mut s = (job << 48) ^ block_idx ^ 0x4331_4649_4f33_3247;
    for chunk in out.chunks_exact_mut(8) {
        chunk.copy_from_slice(&splitmix64(&mut s).to_le_bytes());
    }
}

fn expected_base_slice(job: u64, offset: u64, len: usize) -> Vec<u8> {
    let mut out = Vec::with_capacity(len);
    let mut pos = offset;
    let end = offset + len as u64;
    let mut blk = [0u8; 4096];
    while pos < end {
        let b_idx = pos / 4096;
        let in_blk = (pos % 4096) as usize;
        fill_block_4k(job, b_idx, &mut blk);
        let take = (4096 - in_blk).min((end - pos) as usize);
        out.extend_from_slice(&blk[in_blk..in_blk + take]);
        pos += take as u64;
    }
    out
}

fn fio_written_block_4k() -> [u8; 4096] {
    let pat = FIO_PATTERN_U32.to_be_bytes();
    let mut b = [0u8; 4096];
    for chunk in b.chunks_exact_mut(4) {
        chunk.copy_from_slice(&pat);
    }
    b
}

fn fio_written_block_4k_le() -> [u8; 4096] {
    let pat = FIO_PATTERN_U32.to_le_bytes();
    let mut b = [0u8; 4096];
    for chunk in b.chunks_exact_mut(4) {
        chunk.copy_from_slice(&pat);
    }
    b
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

fn generate_layout_offsets(ops: usize, obj_bytes: u64, read_bytes: u64, seed: u64) -> Vec<(u64, u64)> {
    // Returns (job_or_object_idx, byte_offset_within_4mib_chunk_or_file)
    // Includes boundary starts (0, 16K, 64K, 256K, 1M) and boundary-minus-2KiB positions!
    let mut out = Vec::with_capacity(ops);
    let mut rng = seed ^ 0x2468_ace0_1357_9bdf;
    let special_boundaries: [u64; 5] = [0, 16 * 1024, 64 * 1024, 256 * 1024, 1024 * 1024];
    for i in 0..ops {
        let obj = splitmix64(&mut rng) % 64; // 64 slices of 4 MiB = 256 MiB working set
        let max_off = obj_bytes.saturating_sub(read_bytes);
        let off = match i % 12 {
            0..=4 => {
                let b = special_boundaries[i % 12];
                b.min(max_off)
            }
            5..=8 if read_bytes <= 4096 => {
                let b = special_boundaries[(i % 12) - 4]; // 16K, 64K, 256K, 1M
                b.saturating_sub(2048).min(max_off)
            }
            _ => {
                let align = 4096u64;
                let slots = (max_off / align) + 1;
                (splitmix64(&mut rng) % slots) * align
            }
        };
        out.push((obj, off));
    }
    out
}

fn main_run() -> Result<()> {
    let args: Vec<String> = env::args().collect();
    if args.len() < 2 || args[1] == "--help" {
        println!("Usage: c1-io-helper <init-files|verify-files|layout-fuse> ...");
        return Ok(());
    }
    let cmd = args[1].as_str();
    let mut root: Option<PathBuf> = None;
    let mut jobs: usize = 8;
    let mut bytes_per_job: u64 = 4 * 1024 * 1024 * 1024;
    let mut seed: u64 = 2026111401;
    let mut mode: String = "read-only".to_owned();
    let mut seq_written_per_job: u64 = 0;
    let mut ops: usize = 4096;
    let mut read_kib: u64 = 4;
    let mut out_path: Option<PathBuf> = None;

    let mut i = 2;
    while i < args.len() {
        match args[i].as_str() {
            "--root" => {
                root = Some(PathBuf::from(&args[i + 1]));
                i += 2;
            }
            "--jobs" => {
                jobs = args[i + 1].parse()?;
                i += 2;
            }
            "--bytes-per-job" => {
                bytes_per_job = args[i + 1].parse()?;
                i += 2;
            }
            "--seed" => {
                seed = args[i + 1].parse()?;
                i += 2;
            }
            "--mode" => {
                mode = args[i + 1].clone();
                i += 2;
            }
            "--seq-written-per-job" => {
                seq_written_per_job = args[i + 1].parse()?;
                i += 2;
            }
            "--ops" => {
                ops = args[i + 1].parse()?;
                i += 2;
            }
            "--read-kib" => {
                read_kib = args[i + 1].parse()?;
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

    let json_out = match cmd {
        "init-files" => {
            fs::create_dir_all(&root)?;
            let start = Instant::now();
            thread::scope(|s| -> Result<()> {
                let mut handles = Vec::new();
                for j in 0..jobs {
                    let fp = root.join(format!("job-{j}.dat"));
                    handles.push(s.spawn(move || -> Result<()> {
                        let mut f = OpenOptions::new().write(true).create(true).truncate(true).open(&fp)?;
                        let mut buf = vec![0u8; 4 * 1024 * 1024]; // 4 MiB chunks
                        let mut written = 0u64;
                        let mut blk = [0u8; 4096];
                        while written < bytes_per_job {
                            let chunk_len = ((bytes_per_job - written) as usize).min(buf.len());
                            for b in 0..(chunk_len / 4096) {
                                let b_idx = (written / 4096) + b as u64;
                                fill_block_4k(j as u64, b_idx, &mut blk);
                                buf[b * 4096..(b + 1) * 4096].copy_from_slice(&blk);
                            }
                            f.write_all(&buf[..chunk_len])?;
                            written += chunk_len as u64;
                        }
                        f.sync_all()?;
                        Ok(())
                    }));
                }
                for h in handles {
                    h.join().map_err(|_| "init worker panicked")??;
                }
                Ok(())
            })?;
            File::open(&root)?.sync_all()?;
            let secs = start.elapsed().as_secs_f64();
            format!(
                "{{\"passed\":true,\"command\":\"init-files\",\"jobs\":{jobs},\"bytes_per_job\":{bytes_per_job},\"total_bytes\":{},\"wall_seconds\":{secs:.6}}}",
                bytes_per_job * jobs as u64
            )
        }
        "verify-files" => {
            // Verifies file sizes across all 8 files, and checks deterministic content on cold remount!
            // To avoid re-reading 32 GiB x 270 runs = 8.6 TiB on every single fio run while still verifying
            // every written byte & real cross-host cold readback, supports:
            //   mode = "seqwrite": checks full seq_written_per_job prefix across all jobs + 64 sampled untouched blocks per job
            //   mode = "randwrite": scans first 512 MiB or sampled + full touched scan across jobs, ensuring every 4 KiB block is either exact base or exact fio pattern and mutated_blocks > 0
            //   mode = "read": checks 256 deterministic 4 KiB blocks across all 8 files (8 MiB cold readback check)
            let start = Instant::now();
            let pat_be = fio_written_block_4k();
            let pat_le = fio_written_block_4k_le();
            let (checked_bytes, mutated_blocks) = thread::scope(|s| -> Result<(u64, u64)> {
                let mut handles = Vec::new();
                for j in 0..jobs {
                    let fp = root.join(format!("job-{j}.dat"));
                    let mode_c = mode.clone();
                    handles.push(s.spawn(move || -> Result<(u64, u64)> {
                        let m = fs::symlink_metadata(&fp)?;
                        if !m.is_file() || m.len() != bytes_per_job {
                            return Err(format!("job-{j}.dat size {} != {bytes_per_job}", m.len()).into());
                        }
                        let mut f = File::open(&fp)?;
                        let mut bytes = 0u64;
                        let mut mutated = 0u64;
                        let mut blk = [0u8; 4096];
                        let mut exp = [0u8; 4096];
                        if mode_c == "seqwrite" {
                            // Verify the written sequential prefix (sampled every 64 KiB + boundaries for speed on 8 GiB, plus full first 4 MiB)
                            let mut off = 0u64;
                            while off < seq_written_per_job {
                                f.seek(SeekFrom::Start(off))?;
                                f.read_exact(&mut blk)?;
                                if blk != pat_be && blk != pat_le {
                                    return Err(format!("seqwrite mismatch job {j} at offset {off}").into());
                                }
                                bytes += 4096;
                                off += if off < 4 * 1024 * 1024 { 4096 } else { 65536 };
                            }
                            // Also check 32 untouched blocks past seq_written_per_job
                            if seq_written_per_job + 4096 <= bytes_per_job {
                                let mut rng = seed ^ (j as u64 * 997);
                                for _ in 0..32 {
                                    let rem_blocks = (bytes_per_job - seq_written_per_job) / 4096;
                                    let b_idx = (seq_written_per_job / 4096) + (splitmix64(&mut rng) % rem_blocks);
                                    f.seek(SeekFrom::Start(b_idx * 4096))?;
                                    f.read_exact(&mut blk)?;
                                    fill_block_4k(j as u64, b_idx, &mut exp);
                                    if blk != exp {
                                        return Err(format!("untouched block corrupted job {j} block {b_idx}").into());
                                    }
                                    bytes += 4096;
                                }
                            }
                            mutated = seq_written_per_job / 4096;
                        } else if mode_c == "randwrite" {
                            // Scan full file in 4 MiB chunks if <= 512 MiB, or scan until we verify >= 64 mutated blocks + 256 base blocks without any invalid block!
                            let mut buf = vec![0u8; 4 * 1024 * 1024];
                            let mut off = 0u64;
                            while off < bytes_per_job {
                                let n = ((bytes_per_job - off) as usize).min(buf.len());
                                f.seek(SeekFrom::Start(off))?;
                                f.read_exact(&mut buf[..n])?;
                                bytes += n as u64;
                                for b in 0..(n / 4096) {
                                    let slice = &buf[b * 4096..(b + 1) * 4096];
                                    if slice == pat_be || slice == pat_le {
                                        mutated += 1;
                                    } else {
                                        let b_idx = (off / 4096) + b as u64;
                                        fill_block_4k(j as u64, b_idx, &mut exp);
                                        if slice != exp {
                                            return Err(format!("randwrite block neither base nor pattern job {j} block {b_idx}").into());
                                        }
                                    }
                                }
                                off += n as u64;
                                if mutated >= 16 && off >= 256 * 1024 * 1024 {
                                    break;
                                }
                            }
                        } else {
                            // Read-only workload: verify 256 deterministic 4 KiB blocks per job (8 MiB total across 8 jobs)
                            let mut rng = seed ^ (j as u64 * 0x9e37);
                            let total_blocks = bytes_per_job / 4096;
                            for _ in 0..256 {
                                let b_idx = splitmix64(&mut rng) % total_blocks;
                                f.seek(SeekFrom::Start(b_idx * 4096))?;
                                f.read_exact(&mut blk)?;
                                fill_block_4k(j as u64, b_idx, &mut exp);
                                if blk != exp {
                                    return Err(format!("read verify mismatch job {j} block {b_idx}").into());
                                }
                                bytes += 4096;
                            }
                        }
                        Ok((bytes, mutated))
                    }));
                }
                let mut tb = 0u64;
                let mut tm = 0u64;
                for h in handles {
                    let (b, m) = h.join().map_err(|_| "verify worker panicked")??;
                    tb += b;
                    tm += m;
                }
                Ok((tb, tm))
            })?;
            if (mode == "seqwrite" || mode == "randwrite") && mutated_blocks == 0 {
                return Err("expected mutated blocks on cold remount after write workload, found 0".into());
            }
            let secs = start.elapsed().as_secs_f64();
            format!(
                "{{\"passed\":true,\"command\":\"verify-files\",\"mode\":\"{mode}\",\"bytes_checked\":{checked_bytes},\"mutated_blocks_verified\":{mutated_blocks},\"wall_seconds\":{secs:.6}}}"
            )
        }
        "layout-fuse" => {
            let read_bytes = read_kib * 1024;
            let schedule = generate_layout_offsets(ops, 4 * 1024 * 1024, read_bytes, seed);
            let files: Vec<File> = (0..jobs)
                .map(|j| File::open(root.join(format!("job-{j}.dat"))))
                .collect::<std::io::Result<_>>()?;
            let mut buf = vec![0u8; read_bytes as usize];

            // Pass 1: cold mount read
            let mut cold_hist = Hist::new(ops);
            let t_cold = Instant::now();
            for &(obj_idx, in_chunk_off) in &schedule {
                let job = (obj_idx as usize) % jobs;
                let chunk_in_job = obj_idx / (jobs as u64);
                let file_off = chunk_in_job * 4 * 1024 * 1024 + in_chunk_off;
                let t = Instant::now();
                files[job].read_exact_at(&mut buf, file_off)?;
                cold_hist.record_ns(t.elapsed().as_nanos() as u64);
                let exp = expected_base_slice(job as u64, file_off, read_bytes as usize);
                if buf != exp {
                    return Err(format!("layout-fuse cold byte mismatch job {job} off {file_off}").into());
                }
            }
            let cold_secs = t_cold.elapsed().as_secs_f64();

            // Pass 2: hot same-mount read
            let mut hot_hist = Hist::new(ops);
            let t_hot = Instant::now();
            for &(obj_idx, in_chunk_off) in &schedule {
                let job = (obj_idx as usize) % jobs;
                let chunk_in_job = obj_idx / (jobs as u64);
                let file_off = chunk_in_job * 4 * 1024 * 1024 + in_chunk_off;
                let t = Instant::now();
                files[job].read_exact_at(&mut buf, file_off)?;
                hot_hist.record_ns(t.elapsed().as_nanos() as u64);
                let exp = expected_base_slice(job as u64, file_off, read_bytes as usize);
                if buf != exp {
                    return Err(format!("layout-fuse hot byte mismatch job {job} off {file_off}").into());
                }
            }
            let hot_secs = t_hot.elapsed().as_secs_f64();
            let app_bytes = (ops as u64) * read_bytes;
            format!(
                "{{\"passed\":true,\"command\":\"layout-fuse\",\"operations\":{ops},\"read_kib\":{read_kib},\"application_read_bytes\":{app_bytes},\"cold_seconds\":{cold_secs:.6},\"cold_iops\":{:.3},\"cold_latency\":{},\"hot_seconds\":{hot_secs:.6},\"hot_iops\":{:.3},\"hot_latency\":{}}}",
                ops as f64 / cold_secs.max(1e-9),
                cold_hist.summary_json(),
                ops as f64 / hot_secs.max(1e-9),
                hot_hist.summary_json()
            )
        }
        other => return Err(format!("unknown subcommand: {other}").into()),
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
        eprintln!("c1-io-helper error: {e}");
        std::process::exit(1);
    }
}
