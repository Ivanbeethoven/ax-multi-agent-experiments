//! Fixed-work filesystem probe. All counters describe application calls/bytes.
use std::{
    env,
    fs::{self, File, OpenOptions},
    io::{Read, Seek, SeekFrom, Write},
    path::{Path, PathBuf},
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

fn path(root: &Path, i: u64) -> PathBuf {
    root.join(format!("d{:06}", i / 256))
        .join(format!("f{i:09}"))
}
fn next(state: &mut u64) -> u64 {
    *state = state
        .wrapping_mul(6364136223846793005)
        .wrapping_add(1442695040888963407);
    *state
}
fn pattern(buf: &mut [u8], index: u64) {
    let mut state = index.wrapping_add(1);
    for chunk in buf.chunks_mut(8) {
        let x = next(&mut state).to_le_bytes();
        chunk.copy_from_slice(&x[..chunk.len()]);
    }
}
fn run() -> Result<(), Box<dyn std::error::Error>> {
    let a: Vec<_> = env::args().collect();
    if a.len() != 8 && a.len() != 10 {
        return Err("usage: ax-fs-probe MODE DIR FILES BYTES OPS SEED SAMPLE_EVERY [ARRIVAL_INTERVAL_NS SLO_NS]; 0 disables sampling".into());
    }
    let mode = a[1].as_str();
    if ![
        "prepare", "verify", "read", "seqread", "sparse4k", "stat", "readdir", "write",
    ]
    .contains(&mode)
    {
        return Err("unknown mode".into());
    }
    let root = Path::new(&a[2]);
    let n: u64 = a[3].parse()?;
    let size: usize = a[4].parse()?;
    let ops: u64 = a[5].parse()?;
    let mut seed: u64 = a[6].parse()?;
    let sample: u64 = a[7].parse()?;
    let interval: u64 = if a.len() == 10 { a[8].parse()? } else { 0 };
    let slo: u64 = if a.len() == 10 { a[9].parse()? } else { 0 };
    if n == 0 || size == 0 || size > 1024 * 1024 * 1024 || ops == 0 {
        return Err("positive counts required; file size <= 1 GiB".into());
    }
    if mode == "sparse4k" && size < 4096 {
        return Err("sparse4k needs files >= 4096 bytes".into());
    }
    if mode == "prepare" {
        fs::create_dir(root)?;
    }
    let mut buf = vec![0u8; size];
    let mut expected = vec![0u8; if mode == "verify" { size } else { 0 }];
    let mut lat = Vec::new();
    let mut response = Vec::new();
    let mut read = 0u64;
    let mut written = 0u64;
    let mut entries = 0u64;
    let mut on_time = 0u64;
    let count = if mode == "prepare" || mode == "verify" {
        n
    } else {
        ops
    };
    if let Ok(value) = env::var("AX_START_UNIX_NS") {
        let target: u128 = value.parse()?;
        let now = SystemTime::now().duration_since(UNIX_EPOCH)?.as_nanos();
        if now >= target {
            return Err("missed common start; use a fresh batch".into());
        }
        std::thread::sleep(Duration::from_nanos((target - now).try_into()?));
    }
    let start = Instant::now();
    for op in 0..count {
        let due = Duration::from_nanos(
            interval
                .checked_mul(op)
                .ok_or("arrival schedule overflow")?,
        );
        if interval != 0 {
            if let Some(wait) = due.checked_sub(start.elapsed()) {
                std::thread::sleep(wait);
            }
        }
        let i = if mode == "prepare" || mode == "verify" || mode == "seqread" {
            op % n
        } else {
            next(&mut seed) % n
        };
        let p = path(root, i);
        let sampled = sample != 0 && op % sample == 0;
        let tick = if sampled { Some(Instant::now()) } else { None };
        match mode {
            "prepare" => {
                if i % 256 == 0 {
                    fs::create_dir(p.parent().unwrap())?;
                }
                pattern(&mut buf, i);
                let mut f = OpenOptions::new().write(true).create_new(true).open(p)?;
                f.write_all(&buf)?;
                f.sync_all()?;
                written += size as u64;
            }
            "verify" => {
                let mut f = File::open(p)?;
                f.read_exact(&mut buf)?;
                pattern(&mut expected, i);
                if buf != expected || f.metadata()?.len() != size as u64 {
                    return Err("corpus content mismatch".into());
                }
                read += size as u64;
            }
            "read" | "seqread" => {
                File::open(p)?.read_exact(&mut buf)?;
                read += size as u64;
            }
            "sparse4k" => {
                let mut f = File::open(p)?;
                let offset = (next(&mut seed) % (size as u64 / 4096)) * 4096;
                f.seek(SeekFrom::Start(offset))?;
                f.read_exact(&mut buf[..4096])?;
                read += 4096;
            }
            "stat" => {
                fs::metadata(p)?;
            }
            "readdir" => {
                for e in fs::read_dir(p.parent().unwrap())? {
                    e?;
                    entries += 1;
                }
            }
            "write" => {
                pattern(&mut buf, i);
                let mut f = OpenOptions::new().write(true).open(p)?;
                f.write_all(&buf)?;
                f.sync_all()?;
                written += size as u64;
            }
            _ => unreachable!(),
        }
        if let Some(t) = tick {
            lat.push(t.elapsed().as_nanos() as u64);
        }
        if interval != 0 {
            let elapsed = start.elapsed().saturating_sub(due).as_nanos() as u64;
            if slo != 0 && elapsed <= slo {
                on_time += 1;
            }
            if sampled {
                response.push(elapsed);
            }
        }
    }
    if mode == "prepare" {
        for i in (0..n).step_by(256) {
            File::open(path(root, i).parent().unwrap())?.sync_all()?;
        }
        File::open(root)?.sync_all()?;
    }
    let elapsed = start.elapsed().as_secs_f64();
    lat.sort_unstable();
    response.sort_unstable();
    let percentile = |q: f64| -> String {
        if lat.is_empty() {
            "null".into()
        } else {
            format!(
                "{}",
                lat[((lat.len() - 1) as f64 * q).ceil() as usize] as f64 / 1000.0
            )
        }
    };
    let response_p95 = if response.is_empty() {
        "null".into()
    } else {
        format!(
            "{}",
            response[((response.len() - 1) as f64 * 0.95).ceil() as usize] as f64 / 1000.0
        )
    };
    let completion = if interval == 0 || slo == 0 {
        "null".into()
    } else {
        format!("{}", on_time as f64 / count as f64)
    };
    println!("{{\"scope\":\"application\",\"mode\":\"{mode}\",\"operations\":{count},\"read_bytes\":{read},\"write_bytes\":{written},\"directory_entries\":{entries},\"wall_seconds\":{elapsed},\"iops\":{},\"latency_sample_every\":{sample},\"latency_samples\":{},\"p95_us\":{},\"p99_us\":{},\"arrival_interval_ns\":{interval},\"slo_ns\":{slo},\"arrival_response_p95_us\":{response_p95},\"on_time_fraction\":{completion}}}", count as f64/elapsed, lat.len(), percentile(0.95), percentile(0.99));
    Ok(())
}
fn main() {
    if let Err(e) = run() {
        eprintln!("{e}");
        std::process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn content_is_deterministic_and_nonzero() {
        let mut a = vec![0; 1024];
        let mut b = a.clone();
        pattern(&mut a, 7);
        pattern(&mut b, 7);
        assert_eq!(a, b);
        assert!(a.iter().any(|x| *x != 0));
        pattern(&mut b, 8);
        assert_ne!(a, b);
    }
    #[test]
    fn shard_boundary() {
        assert_eq!(
            path(Path::new("root"), 256),
            Path::new("root/d000001/f000000256")
        );
    }
}
