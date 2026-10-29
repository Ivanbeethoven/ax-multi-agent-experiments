use serde_json::{json, Value};
mod pacing;
use sha2::{Digest, Sha256};
use std::{
    collections::HashMap,
    ffi::CString,
    fs::{self, File, OpenOptions},
    io::{self, BufRead, BufReader},
    os::unix::{
        fs::{MetadataExt, PermissionsExt},
        io::{AsRawFd, FromRawFd},
    },
    path::{Path, PathBuf},
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

struct Replay {
    root: PathBuf,
    blobs: PathBuf,
    handles: HashMap<i64, File>,
    read: u64,
    write: u64,
    performance: bool,
}
impl Replay {
    fn path(&self, v: &Value, follow: bool) -> io::Result<PathBuf> {
        let s = string(v);
        if !s.starts_with('/') || s.split('/').any(|x| x == "..") {
            return Err(invalid("unsafe path"));
        }
        let p = self.root.join(s.trim_start_matches('/'));
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
        let key = string(&v["blob"]);
        if key.len() != 64 || !key.bytes().all(|b| b.is_ascii_hexdigit()) {
            return Err(invalid("bad payload key"));
        }
        let bytes = fs::read(self.blobs.join(key))?;
        if (!self.performance && format!("{:x}", Sha256::digest(&bytes)) != key)
            || bytes.len() as u64 != v["len"].as_u64().unwrap()
        {
            return Err(invalid("payload corrupt"));
        }
        Ok(bytes)
    }
    fn operation(&mut self, e: &Value) -> io::Result<Value> {
        let a = e["args"].as_array().unwrap();
        let op = string(&e["op"]);
        match op {
            "init" | "destroy" => Ok(Value::Null),
            "open" | "create" | "opendir" => {
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
                    self.handles
                        .insert(num(&e["result"]), unsafe { File::from_raw_fd(fd) });
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
                let mut data = vec![0; len];
                let n = check(unsafe {
                    libc::pread(
                        self.fd(&a[3])?.as_raw_fd(),
                        data.as_mut_ptr().cast(),
                        len,
                        num(&a[2]),
                    )
                } as i64)? as usize;
                data.truncate(n);
                self.read += n as u64;
                if self.performance { Ok(json!({"len":n})) }
                else { Ok(json!({"len":n,"sha256":format!("{:x}",Sha256::digest(data))})) }
            }
            "write" => {
                let data = self.payload(&a[1])?;
                let n = check(unsafe {
                    libc::pwrite(
                        self.fd(&a[3])?.as_raw_fd(),
                        data.as_ptr().cast(),
                        data.len(),
                        num(&a[2]),
                    )
                } as i64)?;
                self.write += n as u64;
                Ok(json!(n))
            }
            "release" | "releasedir" => {
                self.handles
                    .remove(&num(&a[1]))
                    .ok_or_else(|| invalid("missing close handle"))?;
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
                if !self.performance && e["errno"] == 0
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
                if a.len() > 2 && !a[2].is_null() && self.handles.contains_key(&num(&a[2])) {
                    self.fd(&a[2])?.set_len(num(&a[1]) as u64)?;
                } else {
                    OpenOptions::new()
                        .write(true)
                        .open(self.path(&a[0], true)?)?
                        .set_len(num(&a[1]) as u64)?;
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
                Ok(json!(0))
            }
            "rmdir" => {
                fs::remove_dir(self.path(&a[0], false)?)?;
                Ok(json!(0))
            }
            "rename" => {
                fs::rename(self.path(&a[0], false)?, self.path(&a[1], false)?)?;
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
                check(unsafe {
                    libc::chown(
                        cpath(&self.path(&a[0], true)?).as_ptr(),
                        num(&a[1]) as u32,
                        num(&a[2]) as u32,
                    )
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
                Ok(json!(0))
            }
            "link" => {
                fs::hard_link(self.path(&a[1], true)?, self.path(&a[0], false)?)?;
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
                data.truncate(n);
                Ok(json!({"len":n,"sha256":format!("{:x}",Sha256::digest(data))}))
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

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let a = std::env::args().skip(1).collect::<Vec<_>>();
    if a.len() < 4 {
        return Err("usage: full-fuse-replay EVENTS BLOBS DEDICATED_ROOT OUTPUT [--performance] [--simulate-llm THINK_PLAN.json]".into());
    }
    let mut performance = false;
    let mut think_path = None;
    let mut option = 4;
    while option < a.len() {
        match a[option].as_str() {
            "--performance" if !performance => performance = true,
            "--simulate-llm" if think_path.is_none() && option + 1 < a.len() => {
                option += 1;
                think_path = Some(Path::new(&a[option]));
            }
            _ => return Err("unknown/duplicate option or missing think plan".into()),
        }
        option += 1;
    }
    let mut pacer = think_path.map(|path| pacing::Pacer::load(path, Path::new(&a[0]))).transpose()?;
    let root = PathBuf::from(&a[2]).canonicalize()?;
    if root.components().count() < 4 || root == std::env::current_dir()? {
        return Err("root too broad".into());
    }
    let output = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&a[3])?;
    let mut r = Replay {
        root,
        blobs: PathBuf::from(&a[1]),
        handles: HashMap::new(),
        read: 0,
        write: 0,
        performance,
    };
    let mut count = 0;
    let mut expected_errors = 0;
    let started = std::time::Instant::now();
    let mut last_op = String::new();
    let mut probe_errno_differences = 0u64;
    for line in BufReader::new(File::open(&a[0])?).lines() {
        let e: Value = serde_json::from_str(&line?)?;
        count += 1;
        if num(&e["seq"]) != count {
            return Err("sequence gap".into());
        }
        if count == 1 && e["op"] != "init" {
            return Err("capture missing init".into());
        }
        last_op = string(&e["op"]).to_owned();
        if let Some(p) = &mut pacer { p.before(count as u64); }
        let actual = r.operation(&e);
        let expected = num(&e["errno"]);
        let good = match &actual {
            Ok(v) if performance => expected == 0 && match last_op.as_str() {
                "read" => v["len"] == e["result"]["len"],
                "write" => v == &e["result"],
                _ => true,
            },
            Err(err) if performance && expected != 0 && matches!(last_op.as_str(), "getxattr" | "listxattr" | "ioctl") && err.raw_os_error().is_some() => {
                if err.raw_os_error() != Some(expected as i32) { probe_errno_differences += 1; }
                true
            },
            Ok(v) => expected == 0 && v == &e["result"],
            Err(err) => expected != 0 && err.raw_os_error() == Some(expected as i32),
        };
        if !good {
            serde_json::to_writer_pretty(
                output,
                &json!({"valid":false,"completed":false,"seq":count,"event":e,"actual":format!("{actual:?}"),
                        "think_time":pacer.as_ref().map(|p|p.report()).unwrap_or(json!({"enabled":false}))}),
            )?;
            return Err(format!("mismatch at {count}: {} {actual:?}", e["op"]).into());
        }
        if expected != 0 {
            expected_errors += 1;
        }
        if count % 65536 == 0 {
            eprintln!("{}", json!({"operations":count,"seconds":started.elapsed().as_secs_f64(),"read_bytes":r.read,"write_bytes":r.write}));
        }
    }
    if count == 0 || last_op != "destroy" {
        return Err("capture missing normal unmount/destroy".into());
    }
    if pacer.as_ref().is_some_and(|p|!p.finished()) { return Err("unused think boundaries".into()); }
    let execution = started.elapsed();
    let slept = pacer.as_ref().map(|p|p.slept()).unwrap_or(std::time::Duration::ZERO);
    serde_json::to_writer_pretty(
        output,
        &json!({"valid":!performance,"completed":true,"mode":if performance {"performance"} else {"strict"},"probe_errno_differences":probe_errno_differences,"operations":count,"expected_errors":expected_errors,"actual_read_bytes":r.read,"actual_write_bytes":r.write,"execution_seconds":execution.as_secs_f64(),
                "active_replay_seconds":execution.saturating_sub(slept).as_secs_f64(),
                "think_time":pacer.as_ref().map(|p|p.report()).unwrap_or(json!({"enabled":false,"planned_total_ms":0,"actual_sleep_seconds":0.0})),
                "remaining_handles":r.handles.len(),"scope":"serialized FUSE callback operations replayed via POSIX; no process scheduling or local lock replay"}),
    )?;
    println!(
        "validated {count} callbacks; read {} write {}",
        r.read, r.write
    );
    Ok(())
}
