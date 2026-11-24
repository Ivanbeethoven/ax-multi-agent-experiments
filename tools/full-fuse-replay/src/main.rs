use serde_json::{json, Value};
mod pacing;
mod parallel;
mod parent_paths;
use std::cell::RefCell;
use std::{
    collections::{BTreeMap, HashMap},
    ffi::CString,
    fs::{self, File, OpenOptions},
    io::{self, BufRead, BufReader},
    os::unix::{
        fs::{MetadataExt, PermissionsExt},
        io::{AsRawFd, FromRawFd},
    },
    path::{Path, PathBuf},
    time::Instant,
};

fn num(v: &Value) -> i64 {
    v.as_i64().expect("integer")
}
fn string(v: &Value) -> &str {
    v.as_str().expect("string")
}
fn cpath(p: &Path) -> CString {
    use std::os::unix::ffi::OsStrExt;
    CString::new(p.as_os_str().as_bytes()).unwrap()
}
fn check(n: i64) -> io::Result<i64> {
    if n < 0 {
        Err(io::Error::last_os_error())
    } else {
        Ok(n)
    }
}
fn invalid(message: &str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message)
}

#[derive(Clone)]
struct Log2Histogram {
    buckets: [u64; 32],
    count: u64,
    sum_us: u128,
    min_us: u64,
    max_us: u64,
}

impl Log2Histogram {
    fn new() -> Self {
        Self {
            buckets: [0; 32],
            count: 0,
            sum_us: 0,
            min_us: u64::MAX,
            max_us: 0,
        }
    }

    fn record_us(&mut self, us: u64) {
        let idx = if us == 0 {
            0
        } else {
            (64 - us.leading_zeros()).min(31) as usize
        };
        self.buckets[idx] += 1;
        self.count += 1;
        self.sum_us += us as u128;
        if us < self.min_us {
            self.min_us = us;
        }
        if us > self.max_us {
            self.max_us = us;
        }
    }

    fn to_json(&self) -> Value {
        let mut bins = Vec::new();
        for (i, &c) in self.buckets.iter().enumerate() {
            if c > 0 {
                let le_us: u64 = if i == 0 {
                    0
                } else if i < 31 {
                    (1u64 << i) - 1
                } else {
                    u64::MAX
                };
                bins.push(json!([le_us, c]));
            }
        }
        json!({
            "format": "log2-us-v1",
            "count": self.count,
            "sum_us": self.sum_us as u64,
            "min_us": if self.count > 0 { json!(self.min_us) } else { Value::Null },
            "max_us": if self.count > 0 { json!(self.max_us) } else { Value::Null },
            "bins": bins
        })
    }
}

struct ReadVersionTracker {
    next_id: u64,
    path_to_id: HashMap<String, u64>,
    handle_to_id: HashMap<i64, u64>,
    file_version: HashMap<u64, u64>,
    intervals: HashMap<(u64, u64), Vec<(u64, u64)>>,
}

impl ReadVersionTracker {
    fn new() -> Self {
        Self {
            next_id: 1,
            path_to_id: HashMap::new(),
            handle_to_id: HashMap::new(),
            file_version: HashMap::new(),
            intervals: HashMap::new(),
        }
    }

    fn ensure_id(&mut self, path: &str) -> u64 {
        if let Some(&id) = self.path_to_id.get(path) {
            id
        } else {
            let id = self.next_id;
            self.next_id += 1;
            self.path_to_id.insert(path.to_owned(), id);
            self.file_version.insert(id, 0);
            id
        }
    }

    fn bump_version(&mut self, id: u64) {
        let v = self.file_version.entry(id).or_insert(0);
        *v += 1;
    }

    fn on_open(&mut self, handle: i64, path: &str, truncated: bool) {
        let id = self.ensure_id(path);
        if truncated {
            self.bump_version(id);
        }
        self.handle_to_id.insert(handle, id);
    }

    fn on_release(&mut self, handle: i64) {
        self.handle_to_id.remove(&handle);
    }

    fn on_write_fd(&mut self, handle: i64, written_bytes: u64) {
        if written_bytes > 0 {
            if let Some(&id) = self.handle_to_id.get(&handle) {
                self.bump_version(id);
            }
        }
    }

    fn on_truncate(&mut self, path: Option<&str>, handle: Option<i64>) {
        if let Some(h) = handle {
            if let Some(&id) = self.handle_to_id.get(&h) {
                self.bump_version(id);
                return;
            }
        }
        if let Some(p) = path {
            let id = self.ensure_id(p);
            self.bump_version(id);
        }
    }

    fn on_path_replace(&mut self, path: &str) {
        if let Some(old_id) = self.path_to_id.remove(path) {
            self.bump_version(old_id);
        }
    }

    fn on_rename(&mut self, old_path: &str, new_path: &str) {
        if old_path == new_path {
            return;
        }
        if let Some(replaced_id) = self.path_to_id.remove(new_path) {
            self.bump_version(replaced_id);
        }
        let moved_id = if let Some(id) = self.path_to_id.remove(old_path) {
            id
        } else {
            let id = self.next_id;
            self.next_id += 1;
            self.file_version.insert(id, 0);
            id
        };
        self.path_to_id.insert(new_path.to_owned(), moved_id);
    }

    fn on_read_fd(&mut self, handle: i64, offset: u64, len: u64) {
        if len == 0 {
            return;
        }
        if let Some(&id) = self.handle_to_id.get(&handle) {
            let ver = *self.file_version.get(&id).unwrap_or(&0);
            self.intervals
                .entry((id, ver))
                .or_default()
                .push((offset, offset + len));
        }
    }

    fn unique_read_bytes(&self) -> u64 {
        let mut total = 0u64;
        for spans in self.intervals.values() {
            if spans.is_empty() {
                continue;
            }
            let mut sorted = spans.clone();
            sorted.sort_unstable_by_key(|x| x.0);
            let (mut cur_s, mut cur_e) = sorted[0];
            for &(s, e) in &sorted[1..] {
                if s <= cur_e {
                    if e > cur_e {
                        cur_e = e;
                    }
                } else {
                    total += cur_e - cur_s;
                    cur_s = s;
                    cur_e = e;
                }
            }
            total += cur_e - cur_s;
        }
        total
    }
}

struct Replay {
    root: PathBuf,
    blobs: PathBuf,
    handles: HashMap<i64, File>,
    read: u64,
    write: u64,
    performance: bool,
    parent_paths: Option<RefCell<parent_paths::ParentPaths>>,
    tracker: ReadVersionTracker,
}

impl Replay {
    fn path(&self, v: &Value, follow: bool) -> io::Result<PathBuf> {
        let s = string(v);
        if !s.starts_with('/') || s.split('/').any(|x| x == "..") {
            return Err(invalid("unsafe path"));
        }
        let p = self.root.join(s.trim_start_matches('/'));
        if let Some(cache) = &self.parent_paths {
            return cache
                .borrow_mut()
                .resolve(&self.root, &p, follow, self.performance);
        }
        // Performance mode accepts only a trusted, preflighted capture in a private root.
        // Avoid extra exists/canonicalize syscalls that are absent from the trace.
        if self.performance {
            return Ok(p);
        }
        let target = if follow && p.exists() {
            p.canonicalize()?
        } else if p == self.root {
            p.clone()
        } else {
            p.parent()
                .unwrap()
                .canonicalize()?
                .join(p.file_name().unwrap())
        };
        if !target.starts_with(&self.root) {
            return Err(invalid("path escapes root"));
        }
        Ok(target)
    }

    fn fd(&self, v: &Value) -> io::Result<&File> {
        self.handles
            .get(&num(v))
            .ok_or_else(|| invalid("missing handle"))
    }

    fn payload(&self, v: &Value) -> io::Result<Vec<u8>> {
        // Blob filenames are opaque 64-hex identifiers from the trace archive; validate length only (never hash).
        let key = string(&v["blob"]);
        if key.len() != 64 || !key.bytes().all(|b| b.is_ascii_hexdigit()) {
            return Err(invalid("bad payload key"));
        }
        let bytes = fs::read(self.blobs.join(key))?;
        if bytes.len() as u64 != v["len"].as_u64().unwrap() {
            return Err(invalid("payload length mismatch"));
        }
        Ok(bytes)
    }

    fn operation(&mut self, e: &Value, preloaded_write: Option<Vec<u8>>) -> io::Result<Value> {
        let a = e["args"].as_array().unwrap();
        let op = string(&e["op"]);
        if let Some(cache) = &self.parent_paths {
            cache.borrow_mut().before(op)
        }
        match op {
            "init" | "destroy" => Ok(Value::Null),
            "open" | "create" | "opendir" => {
                let logical_path = string(&a[0]);
                let p = self.path(&a[0], true)?;
                let (flags, mode) = match op {
                    "open" => (num(&a[1]) as i32, 0),
                    "create" => (
                        libc::O_CREAT | libc::O_RDWR | libc::O_TRUNC,
                        num(&a[1]) as u32,
                    ),
                    _ => (libc::O_RDONLY | libc::O_DIRECTORY, 0),
                };
                let fd =
                    check(unsafe { libc::open(cpath(&p).as_ptr(), flags, mode) } as i64)? as i32;
                if e["errno"] == 0 {
                    let handle_id = num(&e["result"]);
                    self.handles
                        .insert(handle_id, unsafe { File::from_raw_fd(fd) });
                    if op != "opendir" {
                        let truncated = op == "create" || (flags & libc::O_TRUNC) != 0;
                        self.tracker.on_open(handle_id, logical_path, truncated);
                    }
                } else {
                    unsafe {
                        libc::close(fd);
                    }
                }
                Ok(e["result"].clone())
            }
            "read" => {
                let len = num(&a[1]) as usize;
                if len > 64 * 1024 * 1024 {
                    return Err(invalid("oversized read"));
                }
                let offset = num(&a[2]);
                let handle_id = num(&a[3]);
                let mut data = vec![0; len];
                let n = check(unsafe {
                    libc::pread(
                        self.fd(&a[3])?.as_raw_fd(),
                        data.as_mut_ptr().cast(),
                        len,
                        offset,
                    )
                } as i64)? as usize;
                self.read += n as u64;
                if offset >= 0 {
                    self.tracker.on_read_fd(handle_id, offset as u64, n as u64);
                }
                Ok(json!({"len": n}))
            }
            "write" => {
                let data = match preloaded_write {
                    Some(buf) => buf,
                    None => self.payload(&a[1])?,
                };
                let handle_id = num(&a[3]);
                let n = check(unsafe {
                    libc::pwrite(
                        self.fd(&a[3])?.as_raw_fd(),
                        data.as_ptr().cast(),
                        data.len(),
                        num(&a[2]),
                    )
                } as i64)?;
                self.write += n as u64;
                self.tracker.on_write_fd(handle_id, n as u64);
                Ok(json!(n))
            }
            "release" | "releasedir" => {
                let handle_id = num(&a[1]);
                self.handles
                    .remove(&handle_id)
                    .ok_or_else(|| invalid("missing close handle"))?;
                if op == "release" {
                    self.tracker.on_release(handle_id);
                }
                Ok(json!(0))
            }
            "flush" => {
                let fd = check(unsafe { libc::dup(self.fd(&a[1])?.as_raw_fd()) } as i64)?;
                check(unsafe { libc::close(fd as i32) } as i64)?;
                Ok(json!(0))
            }
            "fsync" | "fsyncdir" => {
                let fd = self.fd(&a[2])?.as_raw_fd();
                check(unsafe {
                    if num(&a[1]) != 0 {
                        libc::fdatasync(fd)
                    } else {
                        libc::fsync(fd)
                    }
                } as i64)?;
                Ok(json!(0))
            }
            "getattr" => {
                let m = if a.len() > 1 && !a[1].is_null() && self.handles.contains_key(&num(&a[1]))
                {
                    self.fd(&a[1])?.metadata()?
                } else {
                    fs::symlink_metadata(self.path(&a[0], false)?)?
                };
                // Device/inode, clocks, ownership and directory allocation are host-dependent.
                let expected = &e["result"];
                if !self.performance
                    && e["errno"] == 0
                    && (m.mode() != num(&expected["st_mode"]) as u32
                        || (!m.is_dir() && m.size() != num(&expected["st_size"]) as u64))
                {
                    return Err(invalid("stat mode/size mismatch"));
                }
                Ok(expected.clone())
            }
            "readdir" => {
                let fd = self.fd(&a[1])?.as_raw_fd();
                let mut names = fs::read_dir(format!("/proc/self/fd/{fd}"))?
                    .map(|x| x.map(|e| e.file_name().into_string().unwrap()))
                    .collect::<io::Result<Vec<_>>>()?;
                names.sort();
                let mut all = vec![".".to_string(), "..".to_string()];
                all.extend(names);
                Ok(json!(all))
            }
            "truncate" => {
                let has_fd =
                    a.len() > 2 && !a[2].is_null() && self.handles.contains_key(&num(&a[2]));
                if has_fd {
                    let h = num(&a[2]);
                    self.fd(&a[2])?.set_len(num(&a[1]) as u64)?;
                    self.tracker.on_truncate(a[0].as_str(), Some(h));
                } else {
                    OpenOptions::new()
                        .write(true)
                        .open(self.path(&a[0], true)?)?
                        .set_len(num(&a[1]) as u64)?;
                    self.tracker.on_truncate(a[0].as_str(), None);
                }
                Ok(json!(0))
            }
            "mkdir" => {
                let p = self.path(&a[0], false)?;
                check(unsafe { libc::mkdir(cpath(&p).as_ptr(), num(&a[1]) as u32) } as i64)?;
                Ok(json!(0))
            }
            "unlink" => {
                fs::remove_file(self.path(&a[0], false)?)?;
                self.tracker.on_path_replace(string(&a[0]));
                Ok(json!(0))
            }
            "rmdir" => {
                fs::remove_dir(self.path(&a[0], false)?)?;
                Ok(json!(0))
            }
            "rename" => {
                fs::rename(self.path(&a[0], false)?, self.path(&a[1], false)?)?;
                self.tracker.on_rename(string(&a[0]), string(&a[1]));
                Ok(json!(0))
            }
            "chmod" => {
                fs::set_permissions(
                    self.path(&a[0], true)?,
                    fs::Permissions::from_mode(num(&a[1]) as u32),
                )?;
                Ok(json!(0))
            }
            "chown" => {
                let p = self.path(&a[0], true)?;
                if num(&e["errno"]) == libc::EPERM as i64 {
                    fs::symlink_metadata(&p)?;
                    return Err(io::Error::from_raw_os_error(libc::EPERM));
                }
                check(unsafe {
                    libc::chown(cpath(&p).as_ptr(), num(&a[1]) as u32, num(&a[2]) as u32)
                } as i64)?;
                Ok(json!(0))
            }
            "utimens" => {
                let p = self.path(&a[0], true)?;
                let ts = if a.len() > 1 && !a[1].is_null() {
                    a[1].as_array()
                        .unwrap()
                        .iter()
                        .map(|v| {
                            let t = v.as_f64().unwrap();
                            libc::timespec {
                                tv_sec: t.floor() as i64,
                                tv_nsec: ((t - t.floor()) * 1e9) as i64,
                            }
                        })
                        .collect::<Vec<_>>()
                } else {
                    vec![]
                };
                check(unsafe {
                    libc::utimensat(
                        libc::AT_FDCWD,
                        cpath(&p).as_ptr(),
                        if ts.is_empty() {
                            std::ptr::null()
                        } else {
                            ts.as_ptr()
                        },
                        0,
                    )
                } as i64)?;
                Ok(json!(0))
            }
            "readlink" => Ok(json!(fs::read_link(self.path(&a[0], false)?)?
                .to_str()
                .ok_or_else(|| invalid("non-UTF8 link"))?)),
            "symlink" => {
                std::os::unix::fs::symlink(string(&a[1]), self.path(&a[0], false)?)?;
                self.tracker.on_path_replace(string(&a[0]));
                Ok(json!(0))
            }
            "link" => {
                fs::hard_link(self.path(&a[1], true)?, self.path(&a[0], false)?)?;
                self.tracker.on_path_replace(string(&a[0]));
                Ok(json!(0))
            }
            "access" => {
                check(unsafe {
                    libc::access(cpath(&self.path(&a[0], true)?).as_ptr(), num(&a[1]) as i32)
                } as i64)?;
                Ok(json!(0))
            }
            "ioctl" => {
                if num(&a[1]) != libc::TCGETS as i64 {
                    return Err(invalid("unsupported ioctl command"));
                }
                let mut term = std::mem::MaybeUninit::<libc::termios>::uninit();
                check(unsafe {
                    libc::ioctl(self.fd(&a[3])?.as_raw_fd(), libc::TCGETS, term.as_mut_ptr())
                } as i64)?;
                Ok(json!(0))
            }
            "getxattr" => {
                let p = cpath(&self.path(&a[0], false)?);
                let name = CString::new(string(&a[1])).unwrap();
                let n = check(unsafe {
                    libc::lgetxattr(p.as_ptr(), name.as_ptr(), std::ptr::null_mut(), 0)
                } as i64)? as usize;
                let mut data = vec![0; n];
                let n = check(unsafe {
                    libc::lgetxattr(p.as_ptr(), name.as_ptr(), data.as_mut_ptr().cast(), n)
                } as i64)? as usize;
                Ok(json!({"len": n}))
            }
            "listxattr" => {
                let p = cpath(&self.path(&a[0], false)?);
                let n =
                    check(unsafe { libc::llistxattr(p.as_ptr(), std::ptr::null_mut(), 0) } as i64)?
                        as usize;
                let mut data = vec![0u8; n];
                check(unsafe { libc::llistxattr(p.as_ptr(), data.as_mut_ptr().cast(), n) } as i64)?;
                Ok(json!(data
                    .split(|b| *b == 0)
                    .filter(|s| !s.is_empty())
                    .map(|s| String::from_utf8(s.to_vec()).unwrap())
                    .collect::<Vec<_>>()))
            }
            "statfs" => {
                let p = cpath(&self.path(&a[0], true)?);
                let mut st = std::mem::MaybeUninit::<libc::statvfs>::uninit();
                check(unsafe { libc::statvfs(p.as_ptr(), st.as_mut_ptr()) } as i64)?;
                Ok(e["result"].clone())
            }
            _ => Err(invalid(&format!("Unsupported operation: {op}"))),
        }
    }
}

// A terminal capability probe on a regular file may be rejected by the FUSE
// transport (EOPNOTSUPP) or the filesystem (ENOTTY). Neither returns data or
// changes the file. Keep every data operation and other errno comparison exact.
fn portable_terminal_probe(event: &Value, actual: &io::Error) -> bool {
    event["op"] == "ioctl"
        && event["args"][1].as_i64() == Some(libc::TCGETS as i64)
        && event["errno"].as_i64() == Some(libc::ENOTTY as i64)
        && actual.raw_os_error() == Some(libc::EOPNOTSUPP)
}

fn outcome_matches(
    event: &Value,
    op: &str,
    expected_errno: i64,
    actual: &io::Result<Value>,
    performance: bool,
    portable_probe: bool,
    probe_errno_differences: &mut u64,
) -> bool {
    match actual {
        Err(err) if portable_probe && portable_terminal_probe(event, err) => {
            *probe_errno_differences += 1;
            true
        }
        Ok(v) if performance => {
            expected_errno == 0
                && match op {
                    "read" | "getxattr" => v["len"] == event["result"]["len"],
                    "write" => v == &event["result"],
                    _ => true,
                }
        }
        Err(err)
            if performance
                && expected_errno == 0
                && op == "listxattr"
                && event["result"].as_array().is_some_and(|a| a.is_empty())
                && err.raw_os_error() == Some(libc::EOPNOTSUPP) =>
        {
            *probe_errno_differences += 1;
            true
        }
        Err(err)
            if performance
                && expected_errno != 0
                && matches!(op, "getxattr" | "listxattr" | "ioctl")
                && err.raw_os_error().is_some() =>
        {
            if err.raw_os_error() != Some(expected_errno as i32) {
                *probe_errno_differences += 1;
            }
            true
        }
        Ok(v) => {
            expected_errno == 0
                && match op {
                    // Hash-free validation compares exact byte lengths on read/getxattr; final tree bytes are compared directly.
                    "read" | "getxattr" => v["len"] == event["result"]["len"],
                    _ => v == &event["result"],
                }
        }
        Err(err) => expected_errno != 0 && err.raw_os_error() == Some(expected_errno as i32),
    }
}

type ReplayError = Box<dyn std::error::Error>;

#[derive(Clone, Copy, Default)]
struct ReplayOptions {
    performance: bool,
    portable_probe: bool,
    cache_parent_fds: bool,
}

fn replay_options(
    args: &[String],
    allow_cli_pacing: bool,
) -> Result<(ReplayOptions, Option<&Path>), ReplayError> {
    let mut opts = ReplayOptions::default();
    let mut think_path = None;
    let mut args = args.iter();
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--performance" if !opts.performance => opts.performance = true,
            "--portable-terminal-probe" if !opts.portable_probe => opts.portable_probe = true,
            "--cache-parent-fds" if !opts.cache_parent_fds => opts.cache_parent_fds = true,
            "--simulate-llm" if allow_cli_pacing && think_path.is_none() => {
                think_path = Some(Path::new(args.next().ok_or("missing think plan")?));
            }
            _ => return Err(format!("unknown or duplicate option: {arg}").into()),
        }
    }
    if opts.performance && opts.portable_probe {
        return Err(
            "portable-terminal-probe is a validating mode, separate from performance".into(),
        );
    }
    Ok((opts, think_path))
}

fn workspace_root(path: &Path) -> Result<PathBuf, ReplayError> {
    let root = path.canonicalize()?;
    if root.components().count() < 4 || root == std::env::current_dir()? {
        return Err("root too broad".into());
    }
    if !root.is_dir() {
        return Err("workspace root must be a directory".into());
    }
    Ok(root)
}

fn main() -> Result<(), ReplayError> {
    const USAGE: &str = concat!(
        "usage: full-fuse-replay EVENTS BLOBS DEDICATED_ROOT OUTPUT [--performance | --portable-terminal-probe] [--cache-parent-fds] [--simulate-llm THINK_PLAN.json]\n",
        "       full-fuse-replay --parallel MANIFEST OUTPUT [--performance | --portable-terminal-probe] [--cache-parent-fds]"
    );
    let a = std::env::args().skip(1).collect::<Vec<_>>();
    if a.first().is_some_and(|arg| arg == "--parallel") {
        if a.len() < 3 {
            return Err(USAGE.into());
        }
        let (options, _) = replay_options(&a[3..], false)?;
        return parallel::run(Path::new(&a[1]), Path::new(&a[2]), options);
    }
    if a.len() < 4 {
        return Err(USAGE.into());
    }
    let (options, think_path) = replay_options(&a[4..], true)?;
    let events = Path::new(&a[0]);
    let pacer = think_path
        .map(|path| pacing::Pacer::load(path, events))
        .transpose()?;
    let root = workspace_root(Path::new(&a[2]))?;
    let output = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&a[3])?;
    replay(
        File::open(events)?,
        root,
        PathBuf::from(&a[1]),
        pacer,
        options,
        output,
        None,
    )
}

fn replay(
    events: File,
    root: PathBuf,
    blobs: PathBuf,
    mut pacer: Option<pacing::Pacer>,
    options: ReplayOptions,
    output: impl io::Write,
    stream: Option<&str>,
) -> Result<(), ReplayError> {
    let ReplayOptions {
        performance,
        portable_probe,
        cache_parent_fds,
    } = options;
    let mut r = Replay {
        root,
        blobs,
        handles: HashMap::new(),
        read: 0,
        write: 0,
        performance,
        parent_paths: cache_parent_fds.then(|| RefCell::new(parent_paths::ParentPaths::new())),
        tracker: ReadVersionTracker::new(),
    };
    let mut count = 0i64;
    let mut expected_errors = 0u64;
    let started = Instant::now();
    let mut fs_op_nanos = 0u128;
    let mut op_counts: BTreeMap<String, u64> = BTreeMap::new();
    let mut errno_counts: BTreeMap<String, u64> = BTreeMap::new();
    let mut hist_all = Log2Histogram::new();
    let mut hist_read = Log2Histogram::new();
    let mut hist_write = Log2Histogram::new();
    let mut hist_meta = Log2Histogram::new();
    let mut last_op = String::new();
    let mut probe_errno_differences = 0u64;

    for line in BufReader::new(events).lines() {
        let e: Value = serde_json::from_str(&line?)?;
        count += 1;
        if num(&e["seq"]) != count {
            return Err("sequence gap".into());
        }
        if count == 1 && e["op"] != "init" {
            return Err("capture missing init".into());
        }
        last_op = string(&e["op"]).to_owned();
        if let Some(p) = &mut pacer {
            p.before(count as u64);
        }
        // Preload write payload outside filesystem syscall timing.
        let preloaded = if last_op == "write" {
            Some(r.payload(&e["args"][1])?)
        } else {
            None
        };
        let op_t0 = Instant::now();
        let actual = r.operation(&e, preloaded);
        let op_dt = op_t0.elapsed();
        if !matches!(last_op.as_str(), "init" | "destroy") {
            fs_op_nanos += op_dt.as_nanos();
            let us = op_dt.as_micros() as u64;
            hist_all.record_us(us);
            match last_op.as_str() {
                "read" => hist_read.record_us(us),
                "write" => hist_write.record_us(us),
                _ => hist_meta.record_us(us),
            }
        }
        *op_counts.entry(last_op.clone()).or_insert(0) += 1;
        let actual_errno = match &actual {
            Ok(_) => 0,
            Err(err) => err.raw_os_error().unwrap_or(-1),
        };
        *errno_counts.entry(actual_errno.to_string()).or_insert(0) += 1;

        let expected = num(&e["errno"]);
        let prev_diffs = probe_errno_differences;
        let good = outcome_matches(
            &e,
            &last_op,
            expected,
            &actual,
            performance,
            portable_probe,
            &mut probe_errno_differences,
        );
        if portable_probe && probe_errno_differences > prev_diffs {
            eprintln!(
                "{}",
                json!({
                    "probe_errno_difference": true,
                    "stream": stream,
                    "seq": count,
                    "op": "ioctl",
                    "request": libc::TCGETS,
                    "expected_errno": libc::ENOTTY,
                    "actual_errno": libc::EOPNOTSUPP
                })
            );
        }
        if !good {
            serde_json::to_writer_pretty(
                output,
                &json!({
                    "valid": false,
                    "completed": false,
                    "seq": count,
                    "event": e,
                    "actual": format!("{actual:?}"),
                    "think_time": pacer.as_ref().map(|p| p.report()).unwrap_or(json!({"enabled": false}))
                }),
            )?;
            return Err(format!("mismatch at {count}: {} {actual:?}", e["op"]).into());
        }
        if expected != 0 {
            expected_errors += 1;
        }
        if count % 65536 == 0 {
            eprintln!(
                "{}",
                json!({
                    "stream": stream,
                    "operations": count,
                    "seconds": started.elapsed().as_secs_f64(),
                    "read_bytes": r.read,
                    "write_bytes": r.write
                })
            );
        }
    }
    if count == 0 || last_op != "destroy" {
        return Err("capture missing normal unmount/destroy".into());
    }
    if pacer.as_ref().is_some_and(|p| !p.finished()) {
        return Err("unused think boundaries".into());
    }
    let execution = started.elapsed();
    let slept = pacer.as_ref().map(|p| p.slept()).unwrap_or_default();
    let unique_read_bytes = r.tracker.unique_read_bytes();
    let repeated_read_bytes = r.read.saturating_sub(unique_read_bytes);
    let repeated_read_ratio = if r.read > 0 {
        Some(1.0 - (unique_read_bytes as f64 / r.read as f64))
    } else {
        None
    };

    serde_json::to_writer_pretty(
        output,
        &json!({
            "valid": !performance,
            "completed": true,
            "hash_free": true,
            "mode": if performance { "performance" } else if portable_probe { "strict-portable-terminal-probe" } else { "strict" },
            "probe_errno_differences": probe_errno_differences,
            "operations": count,
            "expected_errors": expected_errors,
            "actual_read_bytes": r.read,
            "actual_write_bytes": r.write,
            "unique_read_bytes": unique_read_bytes,
            "repeated_read_bytes": repeated_read_bytes,
            "repeated_read_ratio": repeated_read_ratio,
            "execution_seconds": execution.as_secs_f64(),
            "active_replay_seconds": execution.saturating_sub(slept).as_secs_f64(),
            "fs_operation_seconds": (fs_op_nanos as f64) / 1e9,
            "op_counts": op_counts,
            "errno_counts": errno_counts,
            "latency_us": {
                "all": hist_all.to_json(),
                "read": hist_read.to_json(),
                "write": hist_write.to_json(),
                "meta": hist_meta.to_json()
            },
            "path_resolution": if cache_parent_fds { "parent-fd-cache" } else { "full-path" },
            "parent_fds": r.parent_paths.as_ref().map(|c| {
                let c = c.borrow();
                json!({
                    "hits": c.hits,
                    "misses": c.misses,
                    "invalidations": c.invalidations,
                    "peak": c.peak,
                    "capacity": parent_paths::ParentPaths::capacity()
                })
            }),
            "think_time": pacer.as_ref().map(|p| p.report()).unwrap_or(json!({"enabled": false, "planned_total_ms": 0, "actual_sleep_seconds": 0.0})),
            "remaining_handles": r.handles.len(),
            "scope": "serialized FUSE callback operations replayed via POSIX; hash-free length/sequence validation"
        }),
    )?;
    if stream.is_none() {
        println!(
            "validated {count} callbacks; read {} write {}",
            r.read, r.write
        );
    }
    Ok(())
}

#[cfg(test)]
mod probe_tests {
    use super::*;

    #[test]
    fn only_the_nonmutating_terminal_probe_is_portable() {
        let event = json!({"op": "ioctl", "args": ["file", libc::TCGETS, 0, 7, 0, null], "errno": libc::ENOTTY});
        assert!(portable_terminal_probe(
            &event,
            &io::Error::from_raw_os_error(libc::EOPNOTSUPP)
        ));
        for code in [libc::ENOENT, libc::EIO, libc::EBADF, libc::EACCES] {
            assert!(!portable_terminal_probe(
                &event,
                &io::Error::from_raw_os_error(code)
            ));
        }
        for (field, value) in [
            ("op", json!("read")),
            ("errno", json!(0)),
            ("args", json!(["file", 0, 0, 7, 0, null])),
        ] {
            let mut changed = event.clone();
            changed[field] = value;
            assert!(!portable_terminal_probe(
                &changed,
                &io::Error::from_raw_os_error(libc::EOPNOTSUPP)
            ));
        }
    }

    #[test]
    fn hash_free_outcome_matches_validates_lengths_without_digest() {
        let read_event = json!({
            "op": "read",
            "errno": 0,
            "result": {"len": 4096, "sha256": "legacy-ignored"}
        });
        let mut diffs = 0;
        assert!(outcome_matches(
            &read_event,
            "read",
            0,
            &Ok(json!({"len": 4096})),
            false,
            false,
            &mut diffs
        ));
        assert!(!outcome_matches(
            &read_event,
            "read",
            0,
            &Ok(json!({"len": 2048})),
            false,
            false,
            &mut diffs
        ));
    }

    #[test]
    fn read_version_tracker_unions_intervals_and_handles_overwrite_and_rename() {
        let mut t = ReadVersionTracker::new();
        t.on_open(10, "/repo/a.py", false);
        // Overlapping reads [0, 100) and [50, 150) => union = 150, total = 200
        t.on_read_fd(10, 0, 100);
        t.on_read_fd(10, 50, 100);
        assert_eq!(t.unique_read_bytes(), 150);

        // Rename preserves file identity & version: reading [100, 200) adds only 50 new unique bytes => 200
        t.on_release(10);
        t.on_rename("/repo/a.py", "/repo/b.py");
        t.on_open(11, "/repo/b.py", false);
        t.on_read_fd(11, 100, 100);
        assert_eq!(t.unique_read_bytes(), 200);

        // Overwrite bumps content version: reading [0, 100) on the new version counts as 100 new unique bytes => 300
        t.on_write_fd(11, 10);
        t.on_read_fd(11, 0, 100);
        assert_eq!(t.unique_read_bytes(), 300);
    }
}
