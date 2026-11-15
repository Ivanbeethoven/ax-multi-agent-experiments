use super::{invalid, pacing, replay, workspace_root, ReplayError, ReplayOptions};
use serde_json::{json, Value};
use std::{
    collections::BTreeSet,
    fs::{File, OpenOptions},
    os::unix::fs::MetadataExt,
    path::{Path, PathBuf},
    sync::{mpsc, Arc, Condvar, Mutex},
    thread,
    time::Instant,
};

const SCHEMA: &str = "full-fuse-parallel-v1";
const MAX_STREAMS: usize = 64;

struct Stream {
    id: String,
    events: PathBuf,
    blobs: PathBuf,
    root: PathBuf,
    think_plan: Option<PathBuf>,
}

fn object<'a>(
    value: &'a Value,
    allowed: &[&str],
) -> Result<&'a serde_json::Map<String, Value>, ReplayError> {
    let row = value.as_object().ok_or("expected a JSON object")?;
    if let Some(key) = row.keys().find(|key| !allowed.contains(&key.as_str())) {
        return Err(format!("unknown manifest field: {key}").into());
    }
    Ok(row)
}

fn path_field(base: &Path, row: &Value, field: &str) -> Result<PathBuf, ReplayError> {
    let raw = row[field]
        .as_str()
        .filter(|s| !s.is_empty())
        .ok_or_else(|| format!("missing or invalid stream field: {field}"))?;
    let path = base.join(raw);
    path.canonicalize()
        .map_err(|err| format!("{field} {}: {err}", path.display()).into())
}

fn disjoint(a: &Path, b: &Path) -> bool {
    !a.starts_with(b) && !b.starts_with(a)
}

fn load(manifest: &Path, output: &Path) -> Result<(Vec<Stream>, PathBuf), ReplayError> {
    let source = manifest.canonicalize()?;
    // A symlinked manifest still resolves relative paths from the path the
    // operator supplied, so it can be used alongside a different mount layout.
    let base = manifest
        .parent()
        .filter(|path| !path.as_os_str().is_empty())
        .unwrap_or(Path::new("."))
        .canonicalize()?;
    let name = output.file_name().ok_or("output must name a file")?;
    let output = output
        .parent()
        .filter(|path| !path.as_os_str().is_empty())
        .unwrap_or(Path::new("."))
        .canonicalize()?
        .join(name);

    let plan: Value = serde_json::from_reader(File::open(&source)?)?;
    object(&plan, &["schema", "streams"])?;
    if plan["schema"] != SCHEMA {
        return Err("unsupported parallel trace schema".into());
    }
    let rows = plan["streams"]
        .as_array()
        .filter(|rows| (2..=MAX_STREAMS).contains(&rows.len()))
        .ok_or_else(|| format!("parallel trace requires 2 to {MAX_STREAMS} streams"))?;

    let mut ids = BTreeSet::new();
    let mut directories = BTreeSet::new();
    let mut streams: Vec<Stream> = Vec::with_capacity(rows.len());
    for row in rows {
        object(row, &["id", "events", "blobs", "root", "think_plan"])?;
        let id = row["id"]
            .as_str()
            .filter(|id| !id.trim().is_empty())
            .ok_or("missing stream id")?;
        if !ids.insert(id) {
            return Err(format!("duplicate stream id: {id}").into());
        }
        let root = workspace_root(&path_field(&base, row, "root")?)?;
        let meta = root.metadata()?;
        if !directories.insert((meta.dev(), meta.ino()))
            || streams.iter().any(|other| !disjoint(&root, &other.root))
        {
            return Err(format!("workspace roots overlap or alias: {}", root.display()).into());
        }
        let events = path_field(&base, row, "events")?;
        let blobs = path_field(&base, row, "blobs")?;
        let think_plan = row
            .get("think_plan")
            .map(|_| path_field(&base, row, "think_plan"))
            .transpose()?;
        if !events.is_file() || !blobs.is_dir() || think_plan.as_ref().is_some_and(|p| !p.is_file())
        {
            return Err(format!(
                "stream {id} needs an events file, blobs directory, and optional think plan file"
            )
            .into());
        }
        streams.push(Stream {
            id: id.into(),
            events,
            blobs,
            root,
            think_plan,
        });
    }
    let inputs = streams.iter().flat_map(|stream| {
        [&stream.events, &stream.blobs]
            .into_iter()
            .chain(stream.think_plan.iter())
    });
    for path in inputs.chain([&source, &output]) {
        if streams.iter().any(|stream| !disjoint(path, &stream.root)) {
            return Err(format!(
                "keep inputs and output outside workspace roots: {}",
                path.display()
            )
            .into());
        }
    }
    if streams
        .iter()
        .any(|stream| output.starts_with(&stream.blobs))
    {
        return Err("keep output outside blobs directories".into());
    }
    Ok((streams, output))
}

#[derive(Clone, Copy)]
enum Start {
    Pending,
    Run,
    Cancel,
}

struct Gate {
    state: Mutex<Start>,
    notification: Condvar,
}

impl Gate {
    fn new() -> Self {
        Self {
            state: Mutex::new(Start::Pending),
            notification: Condvar::new(),
        }
    }

    fn wait(&self) -> Start {
        *self
            .notification
            .wait_while(self.state.lock().unwrap(), |state| {
                matches!(state, Start::Pending)
            })
            .unwrap()
    }

    fn release(&self, state: Start) {
        *self.state.lock().unwrap() = state;
        self.notification.notify_all();
    }
}

struct Played {
    start: Instant,
    finish: Instant,
    report: Value,
    error: Option<String>,
}

fn worker(
    stream: Stream,
    opts: ReplayOptions,
    gate: Arc<Gate>,
    ready: mpsc::Sender<Result<(), String>>,
) -> Option<Played> {
    let prepare = || -> Result<_, ReplayError> {
        let pacer = stream
            .think_plan
            .as_ref()
            .map(|path| pacing::Pacer::load(path, &stream.events))
            .transpose()?;
        Ok((File::open(&stream.events)?, pacer))
    };
    let (events, pacer) = match prepare() {
        Ok(inputs) => inputs,
        Err(err) => {
            let _ = ready.send(Err(format!("stream {}: {err}", stream.id)));
            return None;
        }
    };
    if ready.send(Ok(())).is_err() {
        return None;
    }
    // A worker waiting at the gate must not keep the readiness channel open.
    drop(ready);
    if !matches!(gate.wait(), Start::Run) {
        return None;
    }

    let mut bytes = Vec::new();
    let start = Instant::now();
    let result = replay(
        events,
        stream.root,
        stream.blobs,
        pacer,
        opts,
        &mut bytes,
        Some(&stream.id),
    );
    let finish = Instant::now();
    let mut error = result.err().map(|err| err.to_string());
    let report: Value = if bytes.is_empty() {
        Value::Null
    } else {
        serde_json::from_slice(&bytes).unwrap_or_else(|err| {
            error = Some(format!("read replay result: {err}"));
            Value::Null
        })
    };
    if error.is_none() && report["remaining_handles"].as_u64() != Some(0) {
        error = Some("stream finished with open handles or no result".into());
    }
    Some(Played {
        start,
        finish,
        report,
        error,
    })
}

pub(super) fn run(manifest: &Path, output: &Path, opts: ReplayOptions) -> Result<(), ReplayError> {
    let (streams, output) = load(manifest, output)?;
    let file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(output)?;
    let count = streams.len();
    let gate = Arc::new(Gate::new());
    let (ready_tx, ready_rx) = mpsc::channel();
    let mut children = Vec::with_capacity(count);
    let mut setup_error = None;
    for (index, stream) in streams.into_iter().enumerate() {
        let id = stream.id.clone();
        let gate = Arc::clone(&gate);
        let ready = ready_tx.clone();
        match thread::Builder::new()
            .name(format!("trace-{index}"))
            .spawn(move || worker(stream, opts, gate, ready))
        {
            Ok(child) => children.push((id, child)),
            Err(err) => {
                setup_error = Some(format!("could not start stream {id}: {err}"));
                break;
            }
        }
    }
    drop(ready_tx);
    if setup_error.is_none() {
        for _ in 0..count {
            match ready_rx.recv() {
                Ok(Ok(())) => {}
                Ok(Err(err)) => {
                    setup_error = Some(err);
                    break;
                }
                Err(_) => {
                    setup_error = Some("worker exited during preparation".into());
                    break;
                }
            }
        }
    }

    let release = Instant::now();
    gate.release(if setup_error.is_none() {
        Start::Run
    } else {
        Start::Cancel
    });

    let mut rows = Vec::with_capacity(count);
    let mut boundaries = Vec::with_capacity(2 * count);
    let mut finish = release;
    let mut completed = 0;
    let mut operations = 0;
    let mut read = 0;
    let mut write = 0;
    let mut repeated_read = 0;
    for (id, child) in children {
        let row = match child.join() {
            Ok(Some(played)) => {
                let offset = |t: Instant| t.duration_since(release).as_secs_f64();
                if played.finish > played.start {
                    boundaries.push((played.start, true));
                    boundaries.push((played.finish, false));
                }
                finish = finish.max(played.finish);
                if played.error.is_none() {
                    completed += 1;
                    operations += played.report["operations"].as_u64().unwrap_or(0);
                    read += played.report["actual_read_bytes"].as_u64().unwrap_or(0);
                    write += played.report["actual_write_bytes"].as_u64().unwrap_or(0);
                    repeated_read += played.report["repeated_read_bytes"].as_u64().unwrap_or(0);
                }
                json!({"id": id, "start_offset_seconds": offset(played.start),
                       "finish_offset_seconds": offset(played.finish), "replay": played.report,
                       "error": played.error})
            }
            Ok(None) => json!({"id": id, "error": "stream did not start", "replay": null}),
            Err(_) => json!({"id": id, "error": "worker panicked", "replay": null}),
        };
        rows.push(row);
    }
    // End a stream before counting any new stream at the same timestamp.
    boundaries.sort_unstable();
    let (mut active, mut peak) = (0, 0);
    for (_, starting) in boundaries {
        if starting {
            active += 1;
        } else {
            active -= 1;
        }
        peak = peak.max(active);
    }
    let success = setup_error.is_none() && completed == count;
    serde_json::to_writer_pretty(
        file,
        &json!({
            "schema": "full-fuse-parallel-result-v1",
            "completed": success,
            "requested_streams": count,
            "completed_streams": completed,
            "peak_active_streams": peak,
            "makespan_seconds": finish.duration_since(release).as_secs_f64(),
            "operations": operations,
            "actual_read_bytes": read,
            "actual_write_bytes": write,
            "repeated_read_bytes": repeated_read,
            "repeated_read_ratio": (read > 0).then(|| repeated_read as f64 / read as f64),
            "setup_error": setup_error,
            "streams": rows,
        }),
    )?;
    if !success {
        return Err(invalid("parallel replay did not complete; see the result file").into());
    }
    println!("completed {completed} trace streams; {operations} callbacks, peak active {peak}");
    Ok(())
}
