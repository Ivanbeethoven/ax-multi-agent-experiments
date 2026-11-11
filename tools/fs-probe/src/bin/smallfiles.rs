//! Fixed-work, byte-checked small-file workloads. No cloud or mount lifecycle.
use std::{
    collections::{BTreeMap, BTreeSet},
    env,
    fs::{self, File, OpenOptions},
    io::{self, BufWriter, Read, Write},
    path::{Path, PathBuf},
    thread,
    time::Instant,
};
type Result<T> = std::result::Result<T, Box<dyn std::error::Error + Send + Sync>>;

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
fn path(root: &Path, index: u64) -> PathBuf {
    root.join("small")
        .join(format!("d{:04}", index % 1024))
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
    let mut bytes = [0; 4096];
    for part in bytes.as_chunks_mut::<8>().0 {
        part.copy_from_slice(&next(&mut state).to_le_bytes());
    }
    bytes
}
fn read_checked(root: &Path, index: u64, version: u64, workspace: u64) -> Result<()> {
    if !fs::symlink_metadata(path(root, index))?.is_file() {
        return Err("regular file required".into());
    }
    let mut bytes = Vec::new();
    File::open(path(root, index))?.read_to_end(&mut bytes)?;
    if bytes != payload(index, version, workspace) {
        return Err(format!("content/length mismatch at {index} version {version}").into());
    }
    Ok(())
}
fn new_file(path: &Path) -> io::Result<File> {
    OpenOptions::new().write(true).create_new(true).open(path)
}
fn set_dir_mode(path: &Path) -> io::Result<()> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(path, fs::Permissions::from_mode(0o755))?;
    }
    let _ = path;
    Ok(())
}
fn set_file_mode(file: &File) -> io::Result<()> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        file.set_permissions(fs::Permissions::from_mode(0o644))?;
    }
    let _ = file;
    Ok(())
}
fn save(path: &Path, data: &str) -> Result<()> {
    let mut f = new_file(path)?;
    f.write_all(data.as_bytes())?;
    f.sync_all()?;
    Ok(())
}
fn directory(path: &Path) -> Result<()> {
    File::open(path)?.sync_all()?;
    Ok(())
}
fn hot_set(n: u64, hot: usize, seed: u64) -> Vec<u64> {
    let mut rng = seed;
    let mut seen = BTreeSet::new();
    let mut result = Vec::new();
    while result.len() < hot {
        let i = bounded(&mut rng, n);
        if seen.insert(i) {
            result.push(i);
        }
    }
    result
}

#[derive(Default)]
struct Stats {
    reads: u64,
    writes: u64,
    io_ns: Vec<u64>,
    app_ns: Vec<u64>,
}
impl Stats {
    fn merge(&mut self, other: Self) {
        self.reads += other.reads;
        self.writes += other.writes;
        self.io_ns.extend(other.io_ns);
        self.app_ns.extend(other.app_ns);
    }
    fn json(mut self, phase: &str, seconds: f64) -> String {
        self.io_ns.sort_unstable();
        self.app_ns.sort_unstable();
        let q = |v: &Vec<u64>, p: usize| {
            if v.is_empty() {
                0
            } else {
                v[(v.len() * p).div_ceil(100).saturating_sub(1)]
            }
        };
        format!("{{\"phase\":\"{phase}\",\"reads\":{},\"writes\":{},\"read_bytes\":{},\"write_bytes\":{},\"wall_seconds\":{seconds},\"io_p50_ns\":{},\"io_p95_ns\":{},\"io_p99_ns\":{},\"application_p99_ns\":{}}}",
            self.reads,self.writes,self.reads*4096,self.writes*4096,q(&self.io_ns,50),q(&self.io_ns,95),q(&self.io_ns,99),q(&self.app_ns,99))
    }
}
struct Worker {
    id: usize,
    files: Vec<u64>,
    versions: Vec<u64>,
    log: BufWriter<File>,
}
struct Config {
    files: u64,
    hot: usize,
    workers: usize,
    reads: usize,
    cow: usize,
    writes: usize,
    mixed: usize,
    seed: u64,
    workspace: u64,
}
fn phase(worker: &mut Worker, c: &Config, root: &Path, name: &str) -> Result<Stats> {
    let (count, writable) = match name {
        "cold-read" | "hot-read" => (c.reads / c.workers, false),
        "lower-prewarm" => (worker.files.len() * 2, false),
        "cow" => (c.cow / c.workers, true),
        "upper-prewarm" | "upper-rewarm" => (c.cow / c.workers * 2, false),
        "hot-write" => (c.writes / c.workers, true),
        "mixed" => (c.mixed / c.workers, false),
        _ => return Err("unknown phase".into()),
    };
    let mut rng = c.seed ^ (worker.id as u64).wrapping_mul(0x517cc1b727220a95);
    let mut stats = Stats::default();
    for op in 0..count {
        let app = Instant::now();
        let limited = matches!(
            name,
            "cow" | "upper-prewarm" | "upper-rewarm" | "hot-write" | "mixed"
        );
        let available = if limited {
            c.cow / c.workers
        } else {
            worker.files.len()
        };
        let slot = if matches!(
            name,
            "lower-prewarm" | "cow" | "upper-prewarm" | "upper-rewarm"
        ) {
            op % available
        } else {
            bounded(&mut rng, available as u64) as usize
        };
        let index = if name == "cold-read" {
            bounded(&mut rng, c.files)
        } else {
            worker.files[slot]
        };
        let write = writable || (name == "mixed" && op % 4 == 3);
        let version = if name == "cold-read" {
            0
        } else {
            worker.versions[slot] + u64::from(write)
        };
        let expected = payload(index, version, c.workspace);
        let io_start = Instant::now();
        if write {
            let mut f = OpenOptions::new().write(true).open(path(root, index))?;
            f.write_all(&expected)?;
            f.sync_all()?;
            drop(f);
            worker.versions[slot] = version;
            stats.writes += 1;
        } else {
            let mut actual = [0; 4096];
            let mut f = File::open(path(root, index))?;
            f.read_exact(&mut actual)?;
            drop(f);
            let io_ns = io_start.elapsed().as_nanos() as u64;
            if actual != expected {
                return Err(format!("read mismatch {name} file {index} version {version}").into());
            }
            let app_ns = app.elapsed().as_nanos() as u64;
            stats.reads += 1;
            stats.io_ns.push(io_ns);
            stats.app_ns.push(app_ns);
            writeln!(
                worker.log,
                "{name},{op},{index},{version},4096,0,{io_ns},{app_ns}"
            )?;
            continue;
        }
        let io_ns = io_start.elapsed().as_nanos() as u64;
        let app_ns = app.elapsed().as_nanos() as u64;
        stats.io_ns.push(io_ns);
        stats.app_ns.push(app_ns);
        writeln!(
            worker.log,
            "{name},{op},{index},{version},0,4096,{io_ns},{app_ns}"
        )?;
    }
    worker.log.flush()?;
    worker.log.get_ref().sync_all()?;
    Ok(stats)
}
fn workload(root: &Path, out: &Path, c: &Config) -> Result<String> {
    if c.hot > c.files as usize
        || c.cow > c.hot
        || c.workers == 0
        || c.cow == 0
        || c.workspace == 0
        || [c.hot, c.reads, c.cow, c.writes, c.mixed]
            .iter()
            .any(|n| *n == 0 || n % c.workers != 0)
        || !(c.mixed / c.workers).is_multiple_of(4)
    {
        return Err(
            "positive balanced worker counts, nonzero workspace, 3:1 mixed operations required"
                .into(),
        );
    }
    let all = hot_set(c.files, c.hot, c.seed);
    let mut workers = Vec::new();
    for id in 0..c.workers {
        let files: Vec<_> = all.iter().skip(id).step_by(c.workers).copied().collect();
        let mut log = BufWriter::new(new_file(&out.join(format!("worker-{id}.csv")))?);
        writeln!(
            log,
            "phase,operation,file,version,read_bytes,write_bytes,io_ns,application_ns"
        )?;
        workers.push(Worker {
            id,
            versions: vec![0; files.len()],
            files,
            log,
        });
    }
    // The exact readset is frozen before measurement and reused by every arm.
    save(
        &out.join("readset.txt"),
        &all.iter().map(|i| format!("{i}\n")).collect::<String>(),
    )?;
    let start = Instant::now();
    let mut phases = Vec::new();
    let mut reads = 0;
    let mut writes = 0;
    for name in [
        "cold-read",
        "lower-prewarm",
        "hot-read",
        "cow",
        "upper-prewarm",
        "hot-write",
        "upper-rewarm",
        "mixed",
    ] {
        let tick = Instant::now();
        let results = thread::scope(|scope| {
            let handles: Vec<_> = workers
                .iter_mut()
                .map(|w| scope.spawn(move || phase(w, c, root, name)))
                .collect();
            handles
                .into_iter()
                .map(|h| {
                    h.join()
                        .map_err(|_| "worker panicked".into())
                        .and_then(|x| x)
                })
                .collect::<Result<Vec<_>>>()
        })?;
        let mut total = Stats::default();
        for s in results {
            total.merge(s);
        }
        reads += total.reads;
        writes += total.writes;
        let value = total.json(name, tick.elapsed().as_secs_f64());
        save(&out.join(format!("phase-{name}.json")), &value)?;
        phases.push(value);
    }
    let mut state = format!("smallfiles-v1\t{}\t{}\n", c.files, c.workspace);
    for w in &workers {
        for (i, version) in w.files.iter().zip(&w.versions) {
            if *version > 0 {
                state += &format!("{i}\t{version}\n");
            }
        }
    }
    save(&out.join("expected-touched.tsv"), &state)?;
    Ok(format!("{{\"completed\":true,\"passed\":true,\"files\":{},\"seed\":{},\"workspace\":{},\"workers\":{},\"hot_files\":{},\"cow_files\":{},\"read_ops\":{},\"write_ops\":{},\"mixed_ops\":{},\"verified_reads\":{reads},\"writes\":{writes},\"actual_read_bytes\":{},\"actual_write_bytes\":{},\"wall_seconds\":{},\"phases\":[{}],\"remote_durability_verified\":false}}",
        c.files,c.seed,c.workspace,c.workers,c.hot,c.cow,c.reads,c.writes,c.mixed,reads*4096,writes*4096,start.elapsed().as_secs_f64(),phases.join(",")))
}
fn main_run() -> Result<()> {
    let a: Vec<_> = env::args().collect();
    if a.len() < 2 || a[1] == "--help" {
        println!("smallfiles prepare|verify-base|run|verify-touched --root ABS --output NEW_ABS [--files 1000000 --hot-files 1024 --workers 8 --read-ops 65536 --cow-files 256 --write-ops 2048 --mixed-ops 8192 --seed 9173 --workspace 1] [--state ABS --expect-base 1]");
        return Ok(());
    }
    if !(a.len() - 2).is_multiple_of(2) {
        return Err("expected option/value pairs".into());
    }
    let mut opts = BTreeMap::new();
    for pair in a[2..].as_chunks::<2>().0 {
        if ![
            "--root",
            "--output",
            "--files",
            "--hot-files",
            "--workers",
            "--read-ops",
            "--cow-files",
            "--write-ops",
            "--mixed-ops",
            "--seed",
            "--workspace",
            "--state",
            "--expect-base",
        ]
        .contains(&pair[0].as_str())
            || opts.insert(pair[0].as_str(), pair[1].as_str()).is_some()
        {
            return Err("unknown/duplicate option".into());
        }
    }
    let num = |key: &str, default: &str| -> Result<u64> {
        Ok(opts.get(key).unwrap_or(&default).parse()?)
    };
    let root = PathBuf::from(*opts.get("--root").ok_or("--root required")?);
    let out = PathBuf::from(*opts.get("--output").ok_or("--output required")?);
    if !root.is_absolute()
        || !out.is_absolute()
        || root == Path::new("/")
        || out.exists()
        || root.is_symlink()
        || out.starts_with(&root)
        || root.starts_with(&out)
    {
        return Err(
            "dedicated absolute dataset and new external evidence directories required".into(),
        );
    }
    let files = num("--files", "1000000")?;
    if files == 0 || files > 100_000_000 {
        return Err("dataset must contain 1..100000000 files".into());
    }
    if opts.get("--expect-base").is_some_and(|v| *v != "1") {
        return Err("--expect-base requires 1".into());
    }
    let command = a[1].as_str();
    if !["prepare", "verify-base", "run", "verify-touched"].contains(&command) {
        return Err("unknown command".into());
    }
    let canonical_parent = fs::canonicalize(out.parent().ok_or("output parent required")?)?;
    let real_root = if root.exists() {
        fs::canonicalize(&root)?
    } else {
        fs::canonicalize(root.parent().ok_or("root parent")?)?
            .join(root.file_name().ok_or("root name")?)
    };
    if real_root != root || canonical_parent.starts_with(&root) {
        return Err("symlink/alias paths are not accepted".into());
    }
    fs::create_dir(&out)?;
    save(
        &out.join("started.json"),
        &format!(
            "{{\"command\":\"{command}\",\"pid\":{},\"automatic_retry\":false}}",
            std::process::id()
        ),
    )?;
    let tick = Instant::now();
    let result = match command {
        "prepare" => {
            fs::create_dir(&root)?;
            set_dir_mode(&root)?;
            let small = root.join("small");
            fs::create_dir(&small)?;
            set_dir_mode(&small)?;
            for d in 0..files.min(1024) {
                let dir = small.join(format!("d{d:04}"));
                fs::create_dir(&dir)?;
                set_dir_mode(&dir)?;
            }
            for i in 0..files {
                let mut f = new_file(&path(&root, i))?;
                set_file_mode(&f)?;
                f.write_all(&payload(i, 0, 0))?;
                f.sync_all()?;
            }
            for d in 0..files.min(1024) {
                directory(&root.join("small").join(format!("d{d:04}")))?;
            }
            directory(&root.join("small"))?;
            directory(&root)?;
            format!("{{\"completed\":true,\"passed\":true,\"files\":{files},\"bytes\":{},\"wall_seconds\":{}}}",files*4096,tick.elapsed().as_secs_f64())
        }
        "verify-base" => {
            if !fs::symlink_metadata(root.join("small"))?.is_dir() {
                return Err("regular directory required".into());
            }
            let expected: BTreeSet<_> = (0..files.min(1024)).map(|d| format!("d{d:04}")).collect();
            let found = fs::read_dir(root.join("small"))?
                .map(|e| Ok(e?.file_name().to_string_lossy().to_string()))
                .collect::<io::Result<BTreeSet<_>>>()?;
            if expected != found {
                return Err("namespace directory mismatch".into());
            }
            for d in 0..files.min(1024) {
                if !fs::symlink_metadata(root.join("small").join(format!("d{d:04}")))?.is_dir() {
                    return Err("regular directory required".into());
                }
                let actual = fs::read_dir(root.join("small").join(format!("d{d:04}")))?
                    .map(|e| Ok(e?.file_name().to_string_lossy().to_string()))
                    .collect::<io::Result<BTreeSet<_>>>()?;
                let names = (d..files)
                    .step_by(1024)
                    .map(|i| format!("f{i:07}"))
                    .collect();
                if actual != names {
                    return Err("namespace filename mismatch".into());
                }
            }
            for i in 0..files {
                read_checked(&root, i, 0, 0)?;
            }
            format!("{{\"completed\":true,\"passed\":true,\"files\":{files},\"bytes_checked\":{},\"wall_seconds\":{}}}",files*4096,tick.elapsed().as_secs_f64())
        }
        "verify-touched" => {
            let state = fs::read_to_string(opts.get("--state").ok_or("--state required")?)?;
            let mut rows = state.lines();
            let header: Vec<_> = rows.next().ok_or("empty state")?.split('\t').collect();
            if header.len() != 3 || header[0] != "smallfiles-v1" {
                return Err("invalid state".into());
            }
            let n: u64 = header[1].parse()?;
            let workspace: u64 = header[2].parse()?;
            if n != files || workspace == 0 {
                return Err("touched state dataset/workspace mismatch".into());
            }
            let mut seen = BTreeSet::new();
            for row in rows {
                let f: Vec<_> = row.split('\t').collect();
                if f.len() != 2 {
                    return Err("invalid state row".into());
                }
                let index: u64 = f[0].parse()?;
                let version: u64 = f[1].parse()?;
                if index >= n || version == 0 || !seen.insert(index) {
                    return Err("invalid/duplicate touched file".into());
                }
                read_checked(
                    &root,
                    index,
                    if opts.contains_key("--expect-base") {
                        0
                    } else {
                        version
                    },
                    workspace,
                )?;
            }
            if seen.is_empty() {
                return Err("empty touched set".into());
            }
            format!(
                "{{\"completed\":true,\"passed\":true,\"checked_files\":{},\"wall_seconds\":{}}}",
                seen.len(),
                tick.elapsed().as_secs_f64()
            )
        }
        "run" => workload(
            &root,
            &out,
            &Config {
                files,
                hot: num("--hot-files", "1024")? as usize,
                workers: num("--workers", "8")? as usize,
                reads: num("--read-ops", "65536")? as usize,
                cow: num("--cow-files", "256")? as usize,
                writes: num("--write-ops", "2048")? as usize,
                mixed: num("--mixed-ops", "8192")? as usize,
                seed: num("--seed", "9173")?,
                workspace: num("--workspace", "1")?,
            },
        )?,
        _ => unreachable!(),
    };
    save(&out.join("result.json"), &result)?;
    directory(&out)?;
    println!("{result}");
    Ok(())
}
fn main() {
    if let Err(error) = main_run() {
        eprintln!("{error}");
        std::process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn versions_and_workspaces_are_distinct() {
        assert_ne!(payload(3, 1, 1), payload(3, 1, 2));
        assert_ne!(payload(3, 1, 1), payload(3, 2, 1));
        assert_eq!(payload(3, 0, 1), payload(3, 0, 2));
    }
    #[test]
    fn hot_sets_are_unique_deterministic_and_distributed() {
        let a = hot_set(1000000, 1024, 9173);
        assert_eq!(a, hot_set(1000000, 1024, 9173));
        assert_eq!(a.iter().collect::<BTreeSet<_>>().len(), 1024);
        assert!(a.iter().any(|i| *i > 900000));
    }
}
