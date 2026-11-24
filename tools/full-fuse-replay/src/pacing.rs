use serde_json::{json, Value};
use std::{
    collections::HashSet,
    fs::File,
    io::{self, BufRead, BufReader},
    path::Path,
    time::{Duration, Instant},
};

fn bad(s: &str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, s)
}
fn uint(v: &Value) -> io::Result<u64> {
    v.as_u64().ok_or_else(|| bad("invalid think-plan integer"))
}

pub struct Pacer {
    plan: Value,
    plan_bytes: u64,
    events_bytes: u64,
    index: usize,
    target: Duration,
    slept: Duration,
    records: Vec<Value>,
}

impl Pacer {
    pub fn load(path: &Path, events: &Path) -> io::Result<Self> {
        let raw = std::fs::read(path)?;
        let plan_bytes = raw.len() as u64;
        let plan: Value = serde_json::from_slice(&raw)?;
        Self::validate(&plan)?;
        // Validate complete trace sequence, lifecycle ops, operation count, and byte length without hashing.
        let mut reader = BufReader::new(File::open(events)?);
        let mut line = Vec::new();
        let mut count = 0u64;
        let mut events_bytes = 0u64;
        let mut last = String::new();
        let positions: HashSet<u64> = plan["boundaries"]
            .as_array()
            .unwrap()
            .iter()
            .map(|b| b["before_seq"].as_u64().unwrap())
            .collect();
        loop {
            line.clear();
            let n = reader.read_until(b'\n', &mut line)?;
            if n == 0 {
                break;
            }
            events_bytes += n as u64;
            let event: Value = serde_json::from_slice(&line)?;
            count += 1;
            if uint(&event["seq"])? != count {
                return Err(bad("sequence gap before paced replay"));
            }
            last = event["op"]
                .as_str()
                .ok_or_else(|| bad("missing op"))?
                .to_owned();
            if (count == 1 && last != "init")
                || (positions.contains(&count) && matches!(last.as_str(), "init" | "destroy"))
            {
                return Err(bad("invalid lifecycle pause boundary"));
            }
        }
        if count != uint(&plan["events_operations"])? || last != "destroy" {
            return Err(bad("think-plan trace count/destroy mismatch"));
        }
        if let Some(expected_bytes) = plan.get("events_bytes").and_then(|v| v.as_u64()) {
            if events_bytes != expected_bytes {
                return Err(bad("think-plan trace byte-length mismatch"));
            }
        } else if plan["schema"] == "trace-think-plan-v2" {
            return Err(bad("trace-think-plan-v2 requires events_bytes"));
        }
        Ok(Self {
            plan,
            plan_bytes,
            events_bytes,
            index: 0,
            target: Duration::ZERO,
            slept: Duration::ZERO,
            records: Vec::new(),
        })
    }

    fn validate(plan: &Value) -> io::Result<()> {
        let schema_ok = matches!(
            plan["schema"].as_str(),
            Some("trace-think-plan-v1" | "trace-think-plan-v2")
        );
        let max_ms = uint(&plan["max_interval_ms"])?;
        let min_ms = plan
            .get("min_interval_ms")
            .and_then(|v| v.as_u64())
            .unwrap_or(0);
        if !schema_ok
            || plan["simulate_llm"] != true
            || uint(&plan["agent_count"])? < 1
            || max_ms == 0
            || max_ms > 15000
            || min_ms > max_ms
            || plan["agent_id"].as_str().unwrap_or("").is_empty()
        {
            return Err(bad("invalid agent think plan"));
        }
        let count = uint(&plan["events_operations"])?;
        let rows = plan["boundaries"]
            .as_array()
            .ok_or_else(|| bad("missing boundaries"))?;
        if rows.is_empty() {
            return Err(bad("empty boundaries"));
        }
        let mut previous = 1;
        let mut sum = 0u64;
        let mut ids = HashSet::new();
        for b in rows {
            let seq = uint(&b["before_seq"])?;
            let delay = uint(&b["delay_ms"])?;
            let id = b["id"].as_str().ok_or_else(|| bad("missing boundary id"))?;
            if seq <= previous
                || seq >= count
                || delay < min_ms
                || delay > max_ms
                || id.is_empty()
                || !ids.insert(id)
                || !matches!(b["kind"].as_str(), Some("tool" | "turn"))
            {
                return Err(bad("invalid pause position/kind/id/range"));
            }
            sum = sum.checked_add(delay).ok_or_else(|| bad("budget overflow"))?;
            previous = seq;
        }
        if sum != uint(&plan["total_think_ms"])? {
            return Err(bad("pause sum differs from fixed budget"));
        }
        Ok(())
    }

    pub fn before(&mut self, seq: u64) {
        self.before_with_sleep(seq, |remaining| {
            let started = Instant::now();
            std::thread::sleep(remaining);
            started.elapsed()
        });
    }

    fn before_with_sleep(&mut self, seq: u64, mut sleep: impl FnMut(Duration) -> Duration) {
        let rows = self.plan["boundaries"].as_array().unwrap();
        if self.index == rows.len() || rows[self.index]["before_seq"].as_u64() != Some(seq) {
            return;
        }
        let row = &rows[self.index];
        self.target += Duration::from_millis(row["delay_ms"].as_u64().unwrap());
        // Repay earlier scheduler oversleep using only later think delays, never filesystem time.
        let remaining = self.target.saturating_sub(self.slept);
        let actual = if remaining.is_zero() {
            Duration::ZERO
        } else {
            sleep(remaining)
        };
        self.slept += actual;
        self.records.push(json!({
            "before_seq": seq,
            "boundary_id": row["id"],
            "planned_delay_ms": row["delay_ms"],
            "requested_sleep_seconds": remaining.as_secs_f64(),
            "actual_sleep_seconds": actual.as_secs_f64()
        }));
        self.index += 1;
    }

    pub fn slept(&self) -> Duration {
        self.slept
    }
    pub fn finished(&self) -> bool {
        self.index == self.plan["boundaries"].as_array().unwrap().len()
    }
    pub fn report(&self) -> Value {
        json!({
            "enabled": true,
            "plan_bytes": self.plan_bytes,
            "events_bytes": self.events_bytes,
            "events_operations": self.plan["events_operations"],
            "agent_id": self.plan["agent_id"],
            "seed": self.plan["seed"],
            "planned_total_ms": self.plan["total_think_ms"],
            "actual_sleep_seconds": self.slept.as_secs_f64(),
            "scheduler_error_seconds": self.slept.as_secs_f64() - self.target.as_secs_f64(),
            "applied_boundaries": self.index,
            "all_boundaries_applied": self.finished(),
            "pauses": self.records
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn plan() -> Value {
        json!({
            "schema": "trace-think-plan-v2",
            "simulate_llm": true,
            "agent_count": 64,
            "agent_id": "a01",
            "min_interval_ms": 3000,
            "max_interval_ms": 15000,
            "events_operations": 5,
            "events_bytes": 100,
            "total_think_ms": 20000,
            "boundaries": [
                {"before_seq": 2, "delay_ms": 7000, "id": "b1", "kind": "tool"},
                {"before_seq": 4, "delay_ms": 13000, "id": "b2", "kind": "turn"}
            ]
        })
    }

    #[test]
    fn rejects_malformed_budget_and_boundaries_and_accepts_short_profile() {
        Pacer::validate(&plan()).unwrap();
        let mut single_agent_plan = plan();
        single_agent_plan["agent_count"] = json!(1);
        Pacer::validate(&single_agent_plan).unwrap();
        let short_plan = json!({
            "schema": "trace-think-plan-v2",
            "simulate_llm": true,
            "agent_count": 8,
            "agent_id": "a01",
            "min_interval_ms": 1000,
            "max_interval_ms": 7000,
            "events_operations": 5,
            "events_bytes": 100,
            "total_think_ms": 8000,
            "boundaries": [
                {"before_seq": 2, "delay_ms": 2500, "id": "b1", "kind": "tool"},
                {"before_seq": 4, "delay_ms": 5500, "id": "b2", "kind": "turn"}
            ]
        });
        Pacer::validate(&short_plan).unwrap();

        for (path, value) in [
            ("/total_think_ms", json!(20001)),
            ("/agent_count", json!(0)),
            ("/max_interval_ms", json!(15001)),
            ("/boundaries/0/delay_ms", json!(2999)),
            ("/boundaries/0/delay_ms", json!(15001)),
            ("/boundaries/1/before_seq", json!(2)),
            ("/boundaries/0/kind", json!("operation")),
            ("/boundaries/1/before_seq", json!(5)),
        ] {
            let mut p = plan();
            *p.pointer_mut(path).unwrap() = value;
            assert!(Pacer::validate(&p).is_err(), "expected failure for {path}");
        }
    }

    #[test]
    fn repays_scheduler_oversleep_at_next_boundary_only() {
        let mut p = Pacer {
            plan: plan(),
            plan_bytes: 128,
            events_bytes: 100,
            index: 0,
            target: Duration::ZERO,
            slept: Duration::ZERO,
            records: Vec::new(),
        };
        p.before_with_sleep(1, |_| panic!("must not sleep outside a tool boundary"));
        p.before_with_sleep(2, |requested| {
            assert_eq!(requested, Duration::from_secs(7));
            requested + Duration::from_millis(500)
        });
        p.before_with_sleep(3, |_| panic!("must not sleep between filesystem operations"));
        p.before_with_sleep(4, |requested| {
            assert_eq!(requested, Duration::from_millis(12500));
            requested
        });
        assert!(p.finished());
        assert_eq!(p.slept(), Duration::from_secs(20));
        assert_eq!(p.records.len(), 2);
        let rep = p.report();
        assert_eq!(rep["scheduler_error_seconds"], 0.0);
        assert!(rep.get("plan_sha256").is_none());
        assert_eq!(rep["plan_bytes"], 128);
    }

    #[test]
    fn loads_and_validates_events_bytes_without_hashes() {
        let dir = std::env::temp_dir().join(format!("ffr-pacer-test-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let events_path = dir.join("events.jsonl");
        let plan_path = dir.join("plan.json");
        let events_content = concat!(
            "{\"seq\":1,\"op\":\"init\",\"args\":[],\"errno\":0,\"result\":null}\n",
            "{\"seq\":2,\"op\":\"getattr\",\"args\":[\"/a\"],\"errno\":0,\"result\":{}}\n",
            "{\"seq\":3,\"op\":\"getattr\",\"args\":[\"/b\"],\"errno\":0,\"result\":{}}\n",
            "{\"seq\":4,\"op\":\"getattr\",\"args\":[\"/c\"],\"errno\":0,\"result\":{}}\n",
            "{\"seq\":5,\"op\":\"destroy\",\"args\":[],\"errno\":0,\"result\":null}\n"
        );
        std::fs::write(&events_path, events_content).unwrap();
        let mut p = plan();
        p["events_bytes"] = json!(events_content.len() as u64);
        std::fs::write(&plan_path, serde_json::to_vec(&p).unwrap()).unwrap();
        let loaded = Pacer::load(&plan_path, &events_path).unwrap();
        assert_eq!(loaded.events_bytes, events_content.len() as u64);

        let mut bad_p = p.clone();
        bad_p["events_bytes"] = json!(events_content.len() as u64 + 1);
        std::fs::write(&plan_path, serde_json::to_vec(&bad_p).unwrap()).unwrap();
        assert!(Pacer::load(&plan_path, &events_path).is_err());
        let _ = std::fs::remove_dir_all(&dir);
    }
}
