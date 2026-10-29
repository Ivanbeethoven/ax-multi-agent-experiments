use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{collections::HashSet, fs::File, io::{self, BufRead, BufReader}, path::Path, time::{Duration, Instant}};

fn bad(s: &str) -> io::Error { io::Error::new(io::ErrorKind::InvalidData, s) }
fn uint(v: &Value) -> io::Result<u64> { v.as_u64().ok_or_else(|| bad("invalid think-plan integer")) }

pub struct Pacer {
    plan: Value,
    plan_sha256: String,
    index: usize,
    target: Duration,
    slept: Duration,
    records: Vec<Value>,
}

impl Pacer {
    pub fn load(path: &Path, events: &Path) -> io::Result<Self> {
        let raw = std::fs::read(path)?;
        let plan: Value = serde_json::from_slice(&raw)?;
        Self::validate(&plan)?;
        // Validate the complete trace binding before any filesystem operation or sleep.
        let mut hash = Sha256::new();
        let mut reader = BufReader::new(File::open(events)?);
        let mut line = Vec::new(); let mut count = 0u64; let mut last = String::new();
        let positions: HashSet<u64> = plan["boundaries"].as_array().unwrap().iter()
            .map(|b| b["before_seq"].as_u64().unwrap()).collect();
        loop {
            line.clear();
            if reader.read_until(b'\n', &mut line)? == 0 { break; }
            hash.update(&line); let event: Value = serde_json::from_slice(&line)?; count += 1;
            if uint(&event["seq"])? != count { return Err(bad("sequence gap before paced replay")); }
            last = event["op"].as_str().ok_or_else(|| bad("missing op"))?.to_owned();
            if (count == 1 && last != "init") || (positions.contains(&count) && matches!(last.as_str(), "init"|"destroy")) {
                return Err(bad("invalid lifecycle pause boundary"));
            }
        }
        if count != uint(&plan["events_operations"])? || last != "destroy"
            || format!("{:x}", hash.finalize()) != plan["events_sha256"].as_str().unwrap_or("") {
            return Err(bad("think-plan trace hash/count/destroy mismatch"));
        }
        Ok(Self { plan, plan_sha256:format!("{:x}",Sha256::digest(raw)), index:0,
                  target:Duration::ZERO, slept:Duration::ZERO, records:Vec::new() })
    }

    fn validate(plan: &Value) -> io::Result<()> {
        if plan["schema"] != "trace-think-plan-v1" || plan["simulate_llm"] != true
            || uint(&plan["agent_count"])? < 2 || uint(&plan["max_interval_ms"])? != 15000
            || plan["agent_id"].as_str().unwrap_or("").is_empty() {
            return Err(bad("invalid multi-agent think plan"));
        }
        let count = uint(&plan["events_operations"])?;
        let rows = plan["boundaries"].as_array().ok_or_else(||bad("missing boundaries"))?;
        if rows.is_empty() { return Err(bad("empty boundaries")); }
        let mut previous = 1; let mut sum = 0u64; let mut ids = HashSet::new();
        for b in rows {
            let seq = uint(&b["before_seq"])?; let delay = uint(&b["delay_ms"])?;
            let id = b["id"].as_str().ok_or_else(||bad("missing boundary id"))?;
            if seq <= previous || seq >= count || delay > 15000 || id.is_empty() || !ids.insert(id)
                || !matches!(b["kind"].as_str(), Some("tool"|"turn")) {
                return Err(bad("invalid pause position/kind/id/range"));
            }
            sum = sum.checked_add(delay).ok_or_else(||bad("budget overflow"))?; previous = seq;
        }
        if sum != uint(&plan["total_think_ms"])? { return Err(bad("pause sum differs from fixed budget")); }
        Ok(())
    }

    pub fn before(&mut self, seq: u64) {
        self.before_with_sleep(seq, |remaining| {
            let started = Instant::now(); std::thread::sleep(remaining); started.elapsed()
        });
    }

    fn before_with_sleep(&mut self, seq: u64, mut sleep: impl FnMut(Duration) -> Duration) {
        let rows = self.plan["boundaries"].as_array().unwrap();
        if self.index == rows.len() || rows[self.index]["before_seq"].as_u64() != Some(seq) { return; }
        let row = &rows[self.index];
        self.target += Duration::from_millis(row["delay_ms"].as_u64().unwrap());
        // Repay earlier scheduler oversleep using only later think delays, never filesystem time.
        let remaining = self.target.saturating_sub(self.slept);
        let actual = if remaining.is_zero() { Duration::ZERO } else { sleep(remaining) };
        self.slept += actual;
        self.records.push(json!({"before_seq":seq,"boundary_id":row["id"],"planned_delay_ms":row["delay_ms"],
                                "requested_sleep_seconds":remaining.as_secs_f64(),"actual_sleep_seconds":actual.as_secs_f64()}));
        self.index += 1;
    }

    pub fn slept(&self) -> Duration { self.slept }
    pub fn finished(&self) -> bool { self.index == self.plan["boundaries"].as_array().unwrap().len() }
    pub fn report(&self) -> Value {
        json!({"enabled":true,"plan_sha256":self.plan_sha256,"agent_id":self.plan["agent_id"],
               "seed":self.plan["seed"],"planned_total_ms":self.plan["total_think_ms"],
               "actual_sleep_seconds":self.slept.as_secs_f64(),
               "scheduler_error_seconds":self.slept.as_secs_f64()-self.target.as_secs_f64(),
               "applied_boundaries":self.index,"all_boundaries_applied":self.finished(),"pauses":self.records})
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn plan() -> Value { json!({"schema":"trace-think-plan-v1","simulate_llm":true,"agent_count":64,
        "agent_id":"a01","max_interval_ms":15000,"events_operations":5,"total_think_ms":20000,
        "boundaries":[{"before_seq":2,"delay_ms":7000,"id":"b1","kind":"tool"},
                      {"before_seq":4,"delay_ms":13000,"id":"b2","kind":"turn"}]}) }
    #[test] fn rejects_malformed_budget_and_boundaries() {
        Pacer::validate(&plan()).unwrap();
        for (path,value) in [("/total_think_ms",json!(20001)),("/agent_count",json!(1)),
            ("/boundaries/0/delay_ms",json!(15001)),("/boundaries/1/before_seq",json!(2)),
            ("/boundaries/0/kind",json!("operation")),("/boundaries/1/before_seq",json!(5))] {
            let mut p=plan(); *p.pointer_mut(path).unwrap()=value; assert!(Pacer::validate(&p).is_err());
        }
    }
    #[test] fn repays_scheduler_oversleep_at_next_boundary_only() {
        let mut p=Pacer { plan:plan(),plan_sha256:"fixture".into(),index:0,target:Duration::ZERO,
                          slept:Duration::ZERO,records:Vec::new() };
        p.before_with_sleep(1, |_|panic!("must not sleep outside a tool boundary"));
        p.before_with_sleep(2, |requested| {
            assert_eq!(requested,Duration::from_secs(7)); requested+Duration::from_millis(500)
        });
        p.before_with_sleep(3, |_|panic!("must not sleep between filesystem operations"));
        p.before_with_sleep(4, |requested| {
            assert_eq!(requested,Duration::from_millis(12500)); requested
        });
        assert!(p.finished()); assert_eq!(p.slept(),Duration::from_secs(20));
        assert_eq!(p.records.len(),2); assert_eq!(p.report()["scheduler_error_seconds"],0.0);
    }
}
