//! Bounded-memory readonly ML corpus replay. Manifest: SHA256<TAB>SIZE<TAB>RELATIVE_PATH.
//! The caller freezes real dataset/task provenance; this tool does not claim to be an agent.
use sha2::{Digest, Sha256};
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
    hash: String,
    size: u64,
    path: PathBuf,
}
fn entry(line: &str) -> Result<Entry, String> {
    let fields: Vec<_> = line.splitn(3, '\t').collect();
    if fields.len() != 3
        || fields[0].len() != 64
        || !fields[0].bytes().all(|b| b.is_ascii_hexdigit())
    {
        return Err("invalid manifest record".into());
    }
    let path = PathBuf::from(fields[2]);
    if path.as_os_str().is_empty()
        || path
            .components()
            .any(|c| !matches!(c, Component::Normal(_)))
    {
        return Err("manifest path must be normal and relative".into());
    }
    Ok(Entry {
        hash: fields[0].to_ascii_lowercase(),
        size: fields[1].parse().map_err(|_| "invalid size")?,
        path,
    })
}
#[derive(Default)]
struct Counts {
    files: u64,
    bytes: u64,
    errors: u64,
    first_error: Option<String>,
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
    let mut buffer = [0u8; 65536];
    let mut n = 0;
    let mut hash = Sha256::new();
    loop {
        let count = file.read(&mut buffer)?;
        if count == 0 {
            break;
        }
        n += count as u64;
        if mode == "verify" {
            hash.update(&buffer[..count]);
        } else {
            std::hint::black_box(&buffer[..count]);
        }
    }
    if n != e.size {
        return Err("short/oversized read".into());
    }
    if mode == "verify" && format!("{:x}", hash.finalize()) != e.hash {
        return Err("content SHA256 mismatch".into());
    }
    Ok(n)
}
fn run() -> Result<(), Box<dyn std::error::Error>> {
    let a: Vec<_> = env::args().collect();
    if a.len() != 7 {
        return Err("usage: ml-corpus-scan ROOT MANIFEST stat|read|verify WORKERS EXPECTED_FILES NEW_OUTPUT".into());
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
                    match access(&root, &e, &mode) {
                        Ok(n) => counts.bytes += n,
                        Err(error) => {
                            counts.errors += 1;
                            if counts.first_error.is_none() {
                                counts.first_error = Some(format!("{}: {error}", e.path.display()));
                            }
                        }
                    }
                }
            }
            counts
        }));
    }
    let mut records = 0u64;
    let mut expected_bytes = 0u64;
    let mut source_error = None;
    let mut sha = Sha256::new();
    let mut batch = Vec::with_capacity(256);
    let source = (|| -> Result<(), Box<dyn std::error::Error>> {
        let mut reader = BufReader::new(File::open(manifest)?);
        let mut line = String::new();
        loop {
            line.clear();
            if reader.read_line(&mut line)? == 0 {
                break;
            }
            sha.update(line.as_bytes());
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
        if total.first_error.is_none() {
            total.first_error = c.first_error;
        }
    }
    let passed = source_error.is_none()
        && records == expected
        && total.files == expected
        && total.errors == 0
        && (mode == "stat" || total.bytes == expected_bytes);
    let value = serde_json::json!({"completed":passed,"mode":mode,"workers":workers,
        "manifest_sha256":format!("{:x}",sha.finalize()),"expected_files":expected,"manifest_records":records,
        "attempted_files":total.files,"actual_read_bytes":total.bytes,"expected_payload_bytes":expected_bytes,
        "errors":total.errors,"first_error":total.first_error,"source_error":source_error,
        "wall_seconds":started.elapsed().as_secs_f64(),"content_verified":passed && mode=="verify",
        "scope":"fixed manifest readonly access; includes dispatch and manifest streaming; not live-agent end-to-end",
        "total_timeout_seconds":null,"queue_batches":workers*2,"batch_files":256});
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
            assert!(entry(&format!("{}\t1\t{name}", "0".repeat(64))).is_err());
        }
        assert!(entry("not-a-hash\t1\tfile").is_err());
        assert!(entry(&format!("{}\t-1\tfile", "0".repeat(64))).is_err());
    }
    #[test]
    fn hashes_actual_bytes_and_rejects_truncation() {
        let root = env::temp_dir().join(format!("ax-ml-scan-{}", std::process::id()));
        fs::create_dir(&root).unwrap();
        fs::write(root.join("file"), b"abc").unwrap();
        let mut e = Entry {
            path: "file".into(),
            size: 3,
            hash: format!("{:x}", Sha256::digest(b"abc")),
        };
        assert_eq!(access(&root, &e, "verify").unwrap(), 3);
        e.hash = "0".repeat(64);
        assert!(access(&root, &e, "verify").is_err());
        e.size = 4;
        assert!(access(&root, &e, "read").is_err());
        fs::remove_file(root.join("file")).unwrap();
        fs::remove_dir(root).unwrap();
    }
}
