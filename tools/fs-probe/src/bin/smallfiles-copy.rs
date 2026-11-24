//! Bounded parallel import of the exact immutable C3 base. No model or mount.
use std::{
    collections::BTreeSet,
    env,
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    os::unix::fs::{MetadataExt, PermissionsExt},
    path::{Path, PathBuf},
    thread,
    time::Instant,
};
type Result<T> = std::result::Result<T, Box<dyn std::error::Error + Send + Sync>>;

fn path(root: &Path, i: u64) -> PathBuf {
    root.join("small")
        .join(format!("d{:04}", i % 1024))
        .join(format!("f{i:07}"))
}
fn payload(i: u64) -> [u8; 4096] {
    let mut state = i;
    let mut result = [0; 4096];
    for part in result.chunks_exact_mut(8) {
        state = state.wrapping_add(0x9e3779b97f4a7c15);
        let mut z = state;
        z = (z ^ (z >> 30)).wrapping_mul(0xbf58476d1ce4e5b9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94d049bb133111eb);
        part.copy_from_slice(&(z ^ (z >> 31)).to_le_bytes());
    }
    result
}
fn names(root: &Path) -> Result<BTreeSet<String>> {
    Ok(fs::read_dir(root)?
        .map(|e| Ok(e?.file_name().into_string().map_err(|_| "non-UTF8 name")?))
        .collect::<Result<_>>()?)
}
fn mode(path: &Path, directory: bool) -> Result<fs::Permissions> {
    let m = fs::symlink_metadata(path)?;
    if (directory && !m.is_dir()) || (!directory && !m.is_file()) || m.uid() != 0 || m.gid() != 0 {
        return Err("Exact root-owned regular C3 files/directories required".into());
    }
    Ok(fs::Permissions::from_mode(m.mode() & 0o7777))
}
fn new_dir(source: &Path, target: &Path) -> Result<()> {
    let permissions = mode(source, true)?;
    fs::create_dir(target)?;
    fs::set_permissions(target, permissions)?;
    Ok(())
}
fn main() -> Result<()> {
    let args: Vec<_> = env::args().collect();
    if args.len() != 6 {
        return Err("smallfiles-copy SOURCE NEW_TARGET NEW_RESULT_JSON FILES WORKERS".into());
    }
    let source = PathBuf::from(&args[1]);
    let target = PathBuf::from(&args[2]);
    let output = PathBuf::from(&args[3]);
    let files: u64 = args[4].parse()?;
    let workers: usize = args[5].parse()?;
    if files == 0 || workers == 0 || workers > 32 || files < workers as u64 {
        return Err("Positive fixed work and 1..32 workers required".into());
    }
    if !source.is_absolute()
        || fs::canonicalize(&source)? != source
        || !target.is_absolute()
        || !output.is_absolute()
        || target.exists()
        || output.exists()
    {
        return Err("Canonical source and new absolute destination/result required".into());
    }
    let target_parent = fs::canonicalize(target.parent().ok_or("target parent")?)?;
    let out_parent = fs::canonicalize(output.parent().ok_or("output parent")?)?;
    if target_parent.join(target.file_name().ok_or("target name")?) != target
        || target.starts_with(&source)
        || source.starts_with(&target)
        || output.starts_with(&target)
        || out_parent.starts_with(&source)
    {
        return Err("Disjoint canonical source/target/evidence required".into());
    }
    let start = Instant::now();
    if names(&source)? != BTreeSet::from(["small".into()]) {
        return Err("Unexpected source root entries".into());
    }
    let expected: BTreeSet<_> = (0..files.min(1024)).map(|d| format!("d{d:04}")).collect();
    if names(&source.join("small"))? != expected {
        return Err("Source directory names differ".into());
    }
    for d in 0..files.min(1024) {
        let expected: BTreeSet<_> = (d..files)
            .step_by(1024)
            .map(|i| format!("f{i:07}"))
            .collect();
        if names(&source.join("small").join(format!("d{d:04}")))? != expected {
            return Err("Source file names differ".into());
        }
    }
    new_dir(&source, &target)?;
    new_dir(&source.join("small"), &target.join("small"))?;
    for d in 0..files.min(1024) {
        let p = format!("d{d:04}");
        new_dir(
            &source.join("small").join(&p),
            &target.join("small").join(&p),
        )?
    }
    thread::scope(|scope| -> Result<()> {
        let mut handles = Vec::new();
        for worker in 0..workers {
            let source = &source;
            let target = &target;
            handles.push(scope.spawn(move || -> Result<()> {
                for i in (worker as u64..files).step_by(workers) {
                    let from = path(source, i);
                    let to = path(target, i);
                    let permissions = mode(&from, false)?;
                    let mut data = Vec::with_capacity(4097);
                    File::open(from)?.take(4097).read_to_end(&mut data)?;
                    if data != payload(i) {
                        return Err(format!("Wrong source bytes at file {i}").into());
                    }
                    let mut dest = OpenOptions::new().write(true).create_new(true).open(to)?;
                    dest.write_all(&data)?;
                    dest.set_permissions(permissions)?;
                    dest.sync_all()?;
                }
                Ok(())
            }));
        }
        let mut error = None;
        for h in handles {
            match h.join() {
                Ok(Ok(())) => (),
                Ok(Err(e)) => {
                    if error.is_none() {
                        error = Some(e)
                    }
                }
                Err(_) => {
                    if error.is_none() {
                        error = Some("copy worker panicked".into())
                    }
                }
            }
        }
        if let Some(e) = error {
            return Err(e);
        }
        Ok(())
    })?;
    for d in 0..files.min(1024) {
        File::open(target.join("small").join(format!("d{d:04}")))?.sync_all()?
    }
    File::open(target.join("small"))?.sync_all()?;
    File::open(&target)?.sync_all()?;
    File::open(target_parent)?.sync_all()?;
    let value = format!(
        "{{\"passed\":true,\"files\":{files},\"actual_read_bytes\":{},\"actual_write_bytes\":{},\"workers\":{workers},\"wall_seconds\":{},\"fsync_each_file\":true,\"scope\":\"complete C3 materialization; original cold readback is separate\"}}\n",
        files * 4096,
        files * 4096,
        start.elapsed().as_secs_f64()
    );
    let mut result = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(output)?;
    result.write_all(value.as_bytes())?;
    result.sync_all()?;
    print!("{value}");
    Ok(())
}
