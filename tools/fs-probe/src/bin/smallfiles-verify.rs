//! Read every C3 file after a cold mount. Never creates data in the tested tree.
use std::{
    collections::{BTreeMap, BTreeSet},
    env,
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    os::unix::fs::{MetadataExt, OpenOptionsExt},
    path::{Path, PathBuf},
    thread,
    time::Instant,
};
type Result<T> = std::result::Result<T, Box<dyn std::error::Error + Send + Sync>>;

fn payload(index: u64, version: u64, workspace: u64) -> [u8; 4096] {
    let mut state = index
        ^ version.wrapping_mul(0xd6e8feb86659fd93)
        ^ if version == 0 {
            0
        } else {
            workspace.rotate_left(17)
        };
    let mut out = [0; 4096];
    for part in out.chunks_exact_mut(8) {
        state = state.wrapping_add(0x9e3779b97f4a7c15);
        let mut z = state;
        z = (z ^ (z >> 30)).wrapping_mul(0xbf58476d1ce4e5b9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94d049bb133111eb);
        part.copy_from_slice(&(z ^ (z >> 31)).to_le_bytes());
    }
    out
}
fn names(path: &Path) -> Result<BTreeSet<String>> {
    fs::read_dir(path)?
        .map(|e| Ok(e?.file_name().into_string().map_err(|_| "non-UTF8 name")?))
        .collect()
}
fn directory(path: &Path) -> Result<()> {
    let m = fs::symlink_metadata(path)?;
    if !m.is_dir() || m.mode() & 0o7777 != 0o755 || m.uid() != 0 || m.gid() != 0 {
        return Err(format!("C3 directory type/mode/owner differs: {}", path.display()).into());
    }
    Ok(())
}
fn state(path: &Path, files: u64, workspace: u64) -> Result<BTreeMap<u64, u64>> {
    if !fs::symlink_metadata(path)?.is_file() {
        return Err("regular state required".into());
    }
    let raw = fs::read_to_string(path)?;
    let mut rows = raw.lines();
    if rows.next() != Some(format!("smallfiles-v1\t{files}\t{workspace}").as_str()) {
        return Err("state dataset/workspace differs".into());
    }
    let mut versions = BTreeMap::new();
    for row in rows {
        let fields: Vec<_> = row.split('\t').collect();
        if fields.len() != 2 {
            return Err("invalid state row".into());
        }
        let index: u64 = fields[0].parse()?;
        let version: u64 = fields[1].parse()?;
        if index >= files || version == 0 || versions.insert(index, version).is_some() {
            return Err("invalid/duplicate touched file".into());
        }
    }
    if versions.is_empty() {
        return Err("empty touched state".into());
    }
    Ok(versions)
}
fn main() -> Result<()> {
    let args: Vec<_> = env::args().collect();
    if args.len() != 7 {
        return Err(
            "smallfiles-verify ROOT STATE_OR_base NEW_RESULT_JSON FILES WORKERS WORKSPACE".into(),
        );
    }
    let root = PathBuf::from(&args[1]);
    let output = PathBuf::from(&args[3]);
    let files: u64 = args[4].parse()?;
    let workers: usize = args[5].parse()?;
    let workspace: u64 = args[6].parse()?;
    if files == 0 || files > 1_000_000 || workers == 0 || workers > 32 || workspace == 0 {
        return Err("bounded file/worker counts and positive workspace required".into());
    }
    if !root.is_absolute()
        || fs::canonicalize(&root)? != root
        || !output.is_absolute()
        || output.exists()
        || output.symlink_metadata().is_ok()
        || output.starts_with(&root)
        || output.file_name().is_none()
        || fs::canonicalize(output.parent().ok_or("output parent required")?)?
            != output.parent().unwrap()
    {
        return Err("canonical root and new outside result required".into());
    }
    let versions = if args[2] == "base" {
        BTreeMap::new()
    } else {
        state(Path::new(&args[2]), files, workspace)?
    };
    let begin = Instant::now();
    directory(&root)?;
    if names(&root)? != BTreeSet::from(["small".to_owned()]) {
        return Err("extra root entry".into());
    }
    directory(&root.join("small"))?;
    let directories = files.min(1024);
    if names(&root.join("small"))? != (0..directories).map(|d| format!("d{d:04}")).collect() {
        return Err("directory set differs".into());
    }
    let checked = thread::scope(|scope| -> Result<u64> {
        let mut handles = Vec::new();
        for worker in 0..workers {
            let root = &root;
            let versions = &versions;
            handles.push(scope.spawn(move || -> Result<u64> {
                let mut count = 0;
                for d in (worker as u64..directories).step_by(workers) {
                    let dir = root.join("small").join(format!("d{d:04}"));
                    directory(&dir)?;
                    if names(&dir)?
                        != (d..files)
                            .step_by(1024)
                            .map(|i| format!("f{i:07}"))
                            .collect()
                    {
                        return Err(format!("filename set differs: {d}").into());
                    }
                    for index in (d..files).step_by(1024) {
                        let path = dir.join(format!("f{index:07}"));
                        let m = fs::symlink_metadata(&path)?;
                        if !m.is_file()
                            || m.len() != 4096
                            || m.nlink() != 1
                            || m.mode() & 0o7777 != 0o644
                            || m.uid() != 0
                            || m.gid() != 0
                        {
                            return Err(format!(
                                "file type/size/links/mode/owner differs: {index}"
                            )
                            .into());
                        }
                        // Linux O_NOFOLLOW; the read-only tree must not redirect a read.
                        let mut f = OpenOptions::new()
                            .read(true)
                            .custom_flags(0x20000)
                            .open(path)?;
                        let mut bytes = Vec::with_capacity(4097);
                        (&mut f).take(4097).read_to_end(&mut bytes)?;
                        if bytes.as_slice()
                            != payload(index, *versions.get(&index).unwrap_or(&0), workspace)
                        {
                            return Err(format!("file bytes differ: {index}").into());
                        }
                        count += 1;
                    }
                }
                Ok(count)
            }));
        }
        let mut total = 0;
        let mut failure = None;
        for handle in handles {
            match handle.join() {
                Ok(Ok(count)) => total += count,
                Ok(Err(e)) => {
                    if failure.is_none() {
                        failure = Some(e);
                    }
                }
                Err(_) => {
                    if failure.is_none() {
                        failure = Some("verifier thread panicked".into());
                    }
                }
            }
        }
        if let Some(e) = failure {
            return Err(e);
        }
        Ok(total)
    })?;
    if checked != files {
        return Err("incomplete full verification".into());
    }
    let result = format!("{{\"passed\":true,\"completed\":true,\"files\":{files},\"bytes_checked\":{},\"touched_files\":{},\"workspace\":{workspace},\"workers\":{workers},\"full_namespace\":true,\"modes_owners_links\":true,\"wall_seconds\":{}}}\n",
        files * 4096, versions.len(), begin.elapsed().as_secs_f64());
    let mut f = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&output)?;
    f.write_all(result.as_bytes())?;
    f.sync_all()?;
    File::open(output.parent().unwrap())?.sync_all()?;
    print!("{result}");
    Ok(())
}
