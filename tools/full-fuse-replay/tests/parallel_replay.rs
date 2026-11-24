use serde_json::{json, Value};
use std::ffi::CString;
use std::fs::{self, File};
use std::io::{self, Write};
use std::os::fd::FromRawFd;
use std::os::unix::ffi::OsStrExt;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Output, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::thread;
use std::time::{Duration, Instant};

static NEXT_TEMP_ID: AtomicU64 = AtomicU64::new(0);
const FIFO_TIMEOUT: Duration = Duration::from_secs(5);

struct TempDir(PathBuf);

impl TempDir {
    fn new() -> Self {
        let id = NEXT_TEMP_ID.fetch_add(1, Ordering::Relaxed);
        let path =
            std::env::temp_dir().join(format!("full-fuse-parallel-{}-{id}", std::process::id()));
        fs::create_dir(&path).expect("create test directory");
        Self(path)
    }

    fn path(&self) -> &Path {
        &self.0
    }
}

impl Drop for TempDir {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn event(seq: u64, op: &str, args: Value, result: Value) -> Value {
    json!({
        "seq": seq,
        "op": op,
        "args": args,
        "result": result,
        "errno": 0,
        "pid": 1,
        "uid": 1,
        "gid": 1,
        "start_ns": seq * 10,
        "end_ns": seq * 10 + 1
    })
}

fn read_trace(path: &str, handle: i64, length: usize) -> Vec<Value> {
    vec![
        event(1, "init", json!([]), Value::Null),
        event(2, "open", json!([path, libc::O_RDONLY]), json!(handle)),
        event(
            3,
            "read",
            json!([path, length, 0, handle]),
            json!({"len": length}),
        ),
        event(4, "release", json!([path, handle]), json!(0)),
        event(5, "destroy", json!([]), Value::Null),
    ]
}

fn unexpected_open_trace(path: &str, handle: i64) -> Vec<Value> {
    vec![
        event(1, "init", json!([]), Value::Null),
        event(2, "open", json!([path, libc::O_RDONLY]), json!(handle)),
        event(3, "destroy", json!([]), Value::Null),
    ]
}

fn create_trace(path: &str, handle: i64) -> Vec<Value> {
    vec![
        event(1, "init", json!([]), Value::Null),
        event(2, "create", json!([path, 0o600]), json!(handle)),
        event(3, "release", json!([path, handle]), json!(0)),
        event(4, "destroy", json!([]), Value::Null),
    ]
}

fn fifo_trace(first: &str, second: &str) -> Vec<Value> {
    vec![
        event(1, "init", json!([]), Value::Null),
        event(2, "open", json!([first, libc::O_RDONLY]), json!(71)),
        event(3, "open", json!([second, libc::O_RDONLY]), json!(72)),
        event(4, "release", json!([first, 71]), json!(0)),
        event(5, "release", json!([second, 72]), json!(0)),
        event(6, "destroy", json!([]), Value::Null),
    ]
}

fn write_events(path: &Path, events: &[Value]) {
    let mut file = File::create(path).expect("create event trace");
    for event in events {
        serde_json::to_writer(&mut file, event).expect("serialize event");
        file.write_all(b"\n").expect("write event separator");
    }
}

fn stream(id: &str, events: &str, blobs: &str, root: &str) -> Value {
    json!({
        "id": id,
        "events": events,
        "blobs": blobs,
        "root": root
    })
}

fn write_manifest(path: &Path, streams: Vec<Value>) {
    let manifest = json!({
        "schema": "full-fuse-parallel-v1",
        "streams": streams
    });
    fs::write(
        path,
        serde_json::to_vec(&manifest).expect("serialize manifest"),
    )
    .expect("write manifest");
}

fn run_parallel(manifest: &Path, output: &Path, options: &[&str]) -> Output {
    Command::new(env!("CARGO_BIN_EXE_full-fuse-replay"))
        .arg("--parallel")
        .arg(manifest)
        .arg(output)
        .args(options)
        .output()
        .expect("run parallel replay")
}

fn root_with_file(base: &Path, stream_id: &str, name: &str, bytes: &[u8]) -> PathBuf {
    let root = base.join("roots").join(stream_id);
    fs::create_dir_all(&root).expect("create stream root");
    fs::write(root.join(name), bytes).expect("write initial file");
    root
}

fn make_stream_inputs(base: &Path, id: &str, events: &[Value]) -> (String, String) {
    let events_name = format!("{id}.events.jsonl");
    let blobs_name = format!("{id}.blobs");
    write_events(&base.join(&events_name), events);
    fs::create_dir(base.join(&blobs_name)).expect("create empty blobs directory");
    (events_name, blobs_name)
}

fn report(output: &Path) -> Value {
    serde_json::from_slice(&fs::read(output).expect("read replay report"))
        .expect("parse replay report")
}

fn stream_row<'a>(report: &'a Value, id: &str) -> &'a Value {
    report["streams"]
        .as_array()
        .expect("stream report rows")
        .iter()
        .find(|row| row["id"] == id)
        .unwrap_or_else(|| panic!("missing report row for stream {id}"))
}

fn create_fifo(path: &Path) {
    let path = CString::new(path.as_os_str().as_bytes()).expect("FIFO path has no NUL");
    let result = unsafe { libc::mkfifo(path.as_ptr(), 0o600) };
    assert_eq!(result, 0, "create FIFO: {}", io::Error::last_os_error());
}

fn writer_when_reader_is_ready(
    path: &Path,
    child: &mut Child,
    deadline: Instant,
) -> io::Result<File> {
    let path = CString::new(path.as_os_str().as_bytes())
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, "FIFO path has NUL"))?;
    loop {
        if let Some(status) = child.try_wait()? {
            return Err(io::Error::new(
                io::ErrorKind::Other,
                format!("replay exited before FIFO opened: {status}"),
            ));
        }
        let fd = unsafe { libc::open(path.as_ptr(), libc::O_WRONLY | libc::O_NONBLOCK) };
        if fd >= 0 {
            return Ok(unsafe { File::from_raw_fd(fd) });
        }
        let error = io::Error::last_os_error();
        if error.raw_os_error() != Some(libc::ENXIO) {
            return Err(error);
        }
        if Instant::now() >= deadline {
            return Err(io::Error::new(
                io::ErrorKind::TimedOut,
                format!(
                    "timed out waiting for FIFO reader: {}",
                    path.to_string_lossy()
                ),
            ));
        }
        thread::sleep(Duration::from_millis(10));
    }
}

fn finish_child(child: &mut Option<Child>, deadline: Instant) -> Output {
    loop {
        let polled = child.as_mut().expect("replay child is present").try_wait();
        let status = match polled {
            Ok(status) => status,
            Err(error) => {
                let mut child = child.take().expect("replay child is present");
                let _ = child.kill();
                let _ = child.wait();
                panic!("poll replay process: {error}");
            }
        };
        if status.is_some() {
            return child
                .take()
                .expect("replay child is present")
                .wait_with_output()
                .expect("collect replay output");
        }
        if Instant::now() >= deadline {
            let mut child = child.take().expect("replay child is present");
            let _ = child.kill();
            let output = child
                .wait_with_output()
                .expect("collect timed-out replay output");
            panic!(
                "parallel replay exceeded its bounded timeout; stdout={} stderr={}",
                String::from_utf8_lossy(&output.stdout),
                String::from_utf8_lossy(&output.stderr)
            );
        }
        thread::sleep(Duration::from_millis(10));
    }
}

fn stop_child_after_fifo_error(child: &mut Option<Child>, error: &io::Error) -> ! {
    let mut child = child.take().expect("replay child is present");
    let _ = child.kill();
    let output = child
        .wait_with_output()
        .expect("collect failed replay output");
    panic!(
        "FIFO rendezvous failed: {error}; stdout={} stderr={}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
}

#[test]
fn parallel_replay_isolates_same_handles_and_names_and_aggregates_results() {
    let temp = TempDir::new();
    let base = temp.path();
    let root_a = root_with_file(base, "a", "same.txt", b"red");
    let root_b = root_with_file(base, "b", "same.txt", b"violet");
    let (events_a, blobs_a) = make_stream_inputs(base, "a", &read_trace("/same.txt", 7, 3));
    let (events_b, blobs_b) = make_stream_inputs(base, "b", &read_trace("/same.txt", 7, 6));
    let manifest = base.join("parallel.json");
    let output = base.join("report.json");
    write_manifest(
        &manifest,
        vec![
            stream("a", &events_a, &blobs_a, "roots/a"),
            stream("b", &events_b, &blobs_b, "roots/b"),
        ],
    );

    let result = run_parallel(&manifest, &output, &[]);
    assert!(
        result.status.success(),
        "parallel replay failed: {}",
        String::from_utf8_lossy(&result.stderr)
    );
    let report = report(&output);
    assert_eq!(report["completed"], true);
    assert_eq!(report["operations"], 10);
    assert_eq!(report["actual_read_bytes"], 9);
    assert_eq!(report["actual_write_bytes"], 0);
    assert_eq!(report["streams"].as_array().unwrap().len(), 2);

    for (expected_id, expected_bytes) in [("a", 3), ("b", 6)] {
        let row = stream_row(&report, expected_id);
        assert_eq!(row["id"], expected_id);
        assert_eq!(row["error"], Value::Null);
        assert!(row["start_offset_seconds"].as_f64().unwrap() >= 0.0);
        assert!(
            row["finish_offset_seconds"].as_f64().unwrap()
                >= row["start_offset_seconds"].as_f64().unwrap()
        );
        assert_eq!(row["replay"]["completed"], true);
        assert_eq!(row["replay"]["operations"], 5);
        assert_eq!(row["replay"]["actual_read_bytes"], expected_bytes);
    }
    assert_eq!(fs::read(root_a.join("same.txt")).unwrap(), b"red");
    assert_eq!(fs::read(root_b.join("same.txt")).unwrap(), b"violet");
}

#[test]
fn failed_stream_is_reported_without_losing_successful_stream_results() {
    let temp = TempDir::new();
    let base = temp.path();
    let root_a = root_with_file(base, "a", "same.txt", b"red");
    let root_b = base.join("roots/b");
    fs::create_dir_all(&root_b).expect("create failing stream root");
    let (events_a, blobs_a) = make_stream_inputs(base, "a", &read_trace("/same.txt", 7, 3));
    let (events_b, blobs_b) =
        make_stream_inputs(base, "b", &unexpected_open_trace("/missing.txt", 7));
    let manifest = base.join("parallel.json");
    let output = base.join("report.json");
    write_manifest(
        &manifest,
        vec![
            stream("a", &events_a, &blobs_a, "roots/a"),
            stream("b", &events_b, &blobs_b, "roots/b"),
        ],
    );

    let result = run_parallel(&manifest, &output, &[]);
    assert!(
        !result.status.success(),
        "parallel replay hid a failed stream"
    );
    let report = report(&output);
    assert_eq!(report["completed"], false);
    assert_eq!(report["completed_streams"], 1);
    assert_eq!(report["operations"], 5);
    assert_eq!(report["actual_read_bytes"], 3);
    assert_eq!(report["actual_write_bytes"], 0);

    let successful = stream_row(&report, "a");
    assert_eq!(successful["error"], Value::Null);
    assert_eq!(successful["replay"]["completed"], true);
    assert_eq!(successful["replay"]["actual_read_bytes"], 3);
    let failed = stream_row(&report, "b");
    assert!(failed["error"].as_str().is_some());
    assert_eq!(failed["replay"]["completed"], false);
    assert_eq!(fs::read(root_a.join("same.txt")).unwrap(), b"red");
}

#[test]
fn parallel_workers_can_be_active_together_on_independent_blocking_fifos() {
    let temp = TempDir::new();
    let base = temp.path();
    let root_a = base.join("roots/a");
    let root_b = base.join("roots/b");
    fs::create_dir_all(&root_a).expect("create first stream root");
    fs::create_dir_all(&root_b).expect("create second stream root");
    for root in [&root_a, &root_b] {
        create_fifo(&root.join("gate-one"));
        create_fifo(&root.join("gate-two"));
    }
    let (events_a, blobs_a) = make_stream_inputs(base, "a", &fifo_trace("/gate-one", "/gate-two"));
    let (events_b, blobs_b) = make_stream_inputs(base, "b", &fifo_trace("/gate-two", "/gate-one"));
    let manifest = base.join("parallel.json");
    let output = base.join("report.json");
    write_manifest(
        &manifest,
        vec![
            stream("a", &events_a, &blobs_a, "roots/a"),
            stream("b", &events_b, &blobs_b, "roots/b"),
        ],
    );

    let mut child = Some(
        Command::new(env!("CARGO_BIN_EXE_full-fuse-replay"))
            .arg("--parallel")
            .arg(&manifest)
            .arg(&output)
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .expect("start parallel replay"),
    );
    let deadline = Instant::now() + FIFO_TIMEOUT;
    let mut writers = Vec::new();
    for fifo in [
        root_a.join("gate-one"),
        root_b.join("gate-two"),
        root_a.join("gate-two"),
        root_b.join("gate-one"),
    ] {
        match writer_when_reader_is_ready(
            &fifo,
            child.as_mut().expect("replay child is present"),
            deadline,
        ) {
            Ok(writer) => writers.push(writer),
            Err(error) => stop_child_after_fifo_error(&mut child, &error),
        }
    }
    drop(writers);

    let result = finish_child(&mut child, deadline);
    assert!(
        result.status.success(),
        "parallel FIFO replay failed: {}",
        String::from_utf8_lossy(&result.stderr)
    );
    let report = report(&output);
    assert_eq!(report["completed"], true);
    assert_eq!(report["peak_active_streams"], 2);
    assert_eq!(report["operations"], 12);
    assert_eq!(report["actual_read_bytes"], 0);
    assert_eq!(report["actual_write_bytes"], 0);
}

fn assert_overlapping_roots_rejected_before_replay(
    base: &Path,
    first_root: &str,
    second_root: &str,
) {
    let events_a = create_trace("/created-a", 71);
    let events_b = create_trace("/created-b", 71);
    let (events_a, blobs_a) = make_stream_inputs(base, "a", &events_a);
    let (events_b, blobs_b) = make_stream_inputs(base, "b", &events_b);
    let manifest = base.join("parallel.json");
    let output = base.join("report.json");
    write_manifest(
        &manifest,
        vec![
            stream("a", &events_a, &blobs_a, first_root),
            stream("b", &events_b, &blobs_b, second_root),
        ],
    );

    let result = run_parallel(&manifest, &output, &[]);
    assert!(!result.status.success(), "overlapping roots were accepted");
    assert!(
        !base.join(first_root).join("created-a").exists(),
        "first stream ran before overlapping roots were rejected"
    );
    assert!(
        !base.join(second_root).join("created-b").exists(),
        "second stream ran before overlapping roots were rejected"
    );
}

#[test]
fn parallel_rejects_shared_roots_before_replay_io() {
    let temp = TempDir::new();
    fs::create_dir_all(temp.path().join("roots/shared")).expect("create shared root");
    assert_overlapping_roots_rejected_before_replay(temp.path(), "roots/shared", "roots/shared");
}

#[test]
fn parallel_rejects_nested_roots_before_replay_io() {
    let temp = TempDir::new();
    fs::create_dir_all(temp.path().join("roots/parent/child")).expect("create nested stream roots");
    assert_overlapping_roots_rejected_before_replay(
        temp.path(),
        "roots/parent",
        "roots/parent/child",
    );
}

#[test]
fn parallel_performance_mode_keeps_aggregate_and_per_stream_counts() {
    let temp = TempDir::new();
    let base = temp.path();
    root_with_file(base, "a", "same.txt", b"red");
    root_with_file(base, "b", "same.txt", b"violet");
    let (events_a, blobs_a) = make_stream_inputs(base, "a", &read_trace("/same.txt", 7, 3));
    let (events_b, blobs_b) = make_stream_inputs(base, "b", &read_trace("/same.txt", 7, 6));
    let manifest = base.join("parallel.json");
    let output = base.join("report.json");
    write_manifest(
        &manifest,
        vec![
            stream("a", &events_a, &blobs_a, "roots/a"),
            stream("b", &events_b, &blobs_b, "roots/b"),
        ],
    );

    let result = run_parallel(&manifest, &output, &["--performance"]);
    assert!(
        result.status.success(),
        "parallel performance replay failed: {}",
        String::from_utf8_lossy(&result.stderr)
    );
    let report = report(&output);
    assert_eq!(report["completed"], true);
    assert_eq!(report["operations"], 10);
    assert_eq!(report["actual_read_bytes"], 9);
    assert_eq!(report["actual_write_bytes"], 0);
    for row in report["streams"].as_array().unwrap() {
        assert_eq!(row["error"], Value::Null);
        assert_eq!(row["replay"]["mode"], "performance");
        assert_eq!(row["replay"]["completed"], true);
        assert_eq!(row["replay"]["valid"], false);
    }
}
