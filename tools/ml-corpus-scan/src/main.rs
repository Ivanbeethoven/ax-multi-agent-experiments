//! Bounded-memory readonly ML corpus replay without any hash/checksum calculation.
//! Manifest format: INDEX<TAB>SIZE<TAB>RELATIVE_PATH (or SIZE<TAB>RELATIVE_PATH).
//! Verification checks file type, canonical containment, exact byte length, and deterministic payload bytes when INDEX is supplied.
use std::{
    env,
    fs::{self, File, OpenOptions},
    io::{self, BufRead, BufReader, Read, Write},
    path::{Component, Path, PathBuf},
    sync::{mpsc, Arc, Mutex},
    thread,
    time::Instant,
};

#[derive(Debug)]
struct Entry {
    index: Option<u64>,
    size: u64,
    path: PathBuf,
}

fn deterministic_payload_4k(i: u64) -> [u8; 4096] {
    let mut state = i;
    let mut result = [0u8; 4096];
    for part in result.chunks_exact_mut(8) {
        state = state.wrapping_add(0x9e3779b97f4a7c15);
        let mut z = state;
        z = (z ^ (z >> 30)).wrapping_mul(0xbf58476d1ce4e5b9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94d049bb133111eb);
        part.copy_from_slice(&(z ^ (z >> 31)).to_le_bytes());
    }
    result
}

fn entry(line: &str) -> Result<Entry, String> {
    let fields: Vec<_> = line.split('\t').collect();
    let (index, size_str, raw_path) = match fields.as_slice() {
        [size_s, rel_p] => (None, *size_s, *rel_p),
        [idx_s, size_s, rel_p] => {
            let idx: u64 = idx_s.parse().map_err(|_| "invalid record index")?;
            (Some(idx), *size_s, *rel_p)
        }
        _ => return Err("invalid manifest record".into()),
    };
    let path = PathBuf::from(raw_path);
    if path.as_os_str().is_empty()
        || path
            .components()
            .any(|c| !matches!(c, Component::Normal(_)))
    {
        return Err("manifest path must be normal and relative".into());
    }
    Ok(Entry {
        index,
        size: size_str.parse().map_err(|_| "invalid size")?,
        path,
    })
}

struct Counts {
    files: u64,
    bytes: u64,
    errors: u64,
    first_error: Option<String>,
    // Power-of-two nanosecond buckets: bounded memory even for 32M files.
    latency_ns_log2: [u64; 64],
}
impl Default for Counts {
    fn default() -> Self {
        Self {
            files: 0,
            bytes: 0,
            errors: 0,
            first_error: None,
            latency_ns_log2: [0; 64],
        }
    }
}

fn access(root: &Path, e: &Entry, mode: &str) -> Result<u64, Box<dyn std::error::Error>> {
    let path = root.join(&e.path);
    let meta = fs::symlink_metadata(&path)?;
    if !meta.file_type().is_file() || meta.len() != e.size {
        return Err("file type/length mismatch".into());
    }
    if mode == "stat" {
        return Ok(0);
    }
    if mode == "verify" && !path.canonicalize()?.starts_with(root) {
        return Err("path escaped root".into());
    }
    let mut file = File::open(path)?;
    if mode == "verify" && e.size == 4096 {
        if let Some(idx) = e.index {
            let mut actual = Vec::with_capacity(4097);
            (&mut file).take(4097).read_to_end(&mut actual)?;
            if actual.len() != 4096 || actual.as_slice() != deterministic_payload_4k(idx) {
                return Err("content payload mismatch".into());
            }
            return Ok(4096);
        }
    }
    let mut buffer = [0u8; 65536];
    let mut n = 0;
    loop {
        let count = file.read(&mut buffer)?;
        if count == 0 {
            break;
        }
        n += count as u64;
        std::hint::black_box(&buffer[..count]);
    }
    if n != e.size {
        return Err("short/oversized read".into());
    }
    Ok(n)
}

fn run() -> Result<(), Box<dyn std::error::Error>> {
    let a: Vec<_> = env::args().collect();
    if a.len() != 7 {
        return Err(
            "usage: ml-corpus-scan ROOT MANIFEST stat|read|verify WORKERS EXPECTED_FILES NEW_OUTPUT"
                .into(),
        );
    }
    let root = PathBuf::from(&a[1]).canonicalize()?;
    let manifest = PathBuf::from(&a[2]).canonicalize()?;
    let mode = a[3].clone();
    let workers: usize = a[4].parse()?;
    let expected: u64 = a[5].parse()?;
    if !["stat", "read", "verify"].contains(&mode.as_str())
        || !(1..=64).contains(&workers)
        || expected == 0
    {
        return Err("invalid workload controls".into());
    }
    let output = PathBuf::from(&a[6]);
    let output_parent = output
        .parent()
        .ok_or("output parent missing")?
        .canonicalize()?;
    if manifest.starts_with(&root) || output_parent.starts_with(&root) {
        return Err("manifest/output must be outside measured corpus".into());
    }
    let mut out = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(output)?;
    let (send, recv) = mpsc::sync_channel::<Vec<Entry>>(workers * 2);
    let recv = Arc::new(Mutex::new(recv));
    let started = Instant::now();
    let mut handles = Vec::new();
    for _ in 0..workers {
        let recv = recv.clone();
        let root = root.clone();
        let mode = mode.clone();
        handles.push(thread::spawn(move || {
            let mut counts = Counts::default();
            loop {
                let batch = recv.lock().unwrap().recv();
                let Ok(batch) = batch else {
                    break;
                };
                for e in batch {
                    counts.files += 1;
                    let access_started = Instant::now();
                    match access(&root, &e, &mode) {
                        Ok(n) => counts.bytes += n,
                        Err(error) => {
                            counts.errors += 1;
                            if counts.first_error.is_none() {
                                counts.first_error = Some(format!("{}: {error}", e.path.display()));
                            }
                        }
                    }
                    let nanos = access_started
                        .elapsed()
                        .as_nanos()
                        .max(1)
                        .min(u64::MAX as u128) as u64;
                    counts.latency_ns_log2[(63 - nanos.leading_zeros()) as usize] += 1;
                }
            }
            counts
        }));
    }
    let mut records = 0u64;
    let mut manifest_bytes = 0u64;
    let mut expected_bytes = 0u64;
    let mut source_error = None;
    let mut batch = Vec::with_capacity(256);
    let source = (|| -> Result<(), Box<dyn std::error::Error>> {
        let mut reader = BufReader::new(File::open(manifest)?);
        let mut line = String::new();
        loop {
            line.clear();
            let read_len = reader.read_line(&mut line)?;
            if read_len == 0 {
                break;
            }
            manifest_bytes += read_len as u64;
            let e = entry(line.trim_end_matches(['\n', '\r'])).map_err(io::Error::other)?;
            records += 1;
            expected_bytes += e.size;
            batch.push(e);
            if batch.len() == 256 {
                send.send(std::mem::replace(&mut batch, Vec::with_capacity(256)))?;
            }
        }
        if !batch.is_empty() {
            send.send(batch)?;
        }
        Ok(())
    })();
    if let Err(error) = source {
        source_error = Some(error.to_string());
    }
    drop(send);
    let mut total = Counts::default();
    for handle in handles {
        let c = handle.join().map_err(|_| "worker panicked")?;
        total.files += c.files;
        total.bytes += c.bytes;
        total.errors += c.errors;
        for (bucket, count) in total.latency_ns_log2.iter_mut().zip(c.latency_ns_log2) {
            *bucket += count;
        }
        if total.first_error.is_none() {
            total.first_error = c.first_error;
        }
    }
    let passed = source_error.is_none()
        && records == expected
        && total.files == expected
        && total.errors == 0
        && (mode == "stat" || total.bytes == expected_bytes);
    let value = serde_json::json!({
        "completed": passed,
        "mode": mode,
        "workers": workers,
        "manifest_bytes": manifest_bytes,
        "expected_files": expected,
        "manifest_records": records,
        "attempted_files": total.files,
        "actual_read_bytes": total.bytes,
        "expected_payload_bytes": expected_bytes,
        "errors": total.errors,
        "first_error": total.first_error,
        "source_error": source_error,
        "access_latency_ns_log2": total.latency_ns_log2.as_slice(),
        "latency_scope": "all attempted accesses including errors; bucket k is [2^k,2^(k+1)) ns; includes stat/open/read, not manifest queue wait",
        "wall_seconds": started.elapsed().as_secs_f64(),
        "content_verified": passed && mode == "verify",
        "scope": "fixed manifest readonly access; includes dispatch and manifest streaming; not live-agent end-to-end",
        "total_timeout_seconds": null,
        "queue_batches": workers * 2,
        "batch_files": 256
    });
    serde_json::to_writer_pretty(&mut out, &value)?;
    out.write_all(b"\n")?;
    println!("{value}");
    if !passed {
        return Err("corpus run rejected".into());
    }
    Ok(())
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    run()
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn reject_escape_and_malformed_records() {
        for name in ["../secret", "/absolute", "a/../../secret", ""] {
            assert!(entry(&format!("0\t1\t{name}")).is_err());
        }
        assert!(entry("not-an-index\t1\tfile").is_err());
        assert!(entry("0\t-1\tfile").is_err());
    }
    #[test]
    fn verifies_actual_bytes_and_rejects_truncation() {
        let root = env::temp_dir().join(format!("ax-ml-scan-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir(&root).unwrap();
        fs::write(root.join("file"), deterministic_payload_4k(7)).unwrap();
        let mut e = Entry {
            index: Some(7),
            path: "file".into(),
            size: 4096,
        };
        assert_eq!(access(&root, &e, "verify").unwrap(), 4096);
        e.index = Some(8);
        assert!(access(&root, &e, "verify").is_err());
        e.index = None;
        e.size = 4097;
        assert!(access(&root, &e, "read").is_err());
        let _ = fs::remove_dir_all(&root);
    }
}
