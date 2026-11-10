//! Actual public-API quota rejection; local objects + in-memory metadata only.
use std::{collections::BTreeMap, path::PathBuf, sync::Arc};
use brewfs::cadapter::localfs::LocalFsBackend;
use brewfs::workspace_overlay::entity::{memory::MemoryEntityBackend, native_lower::NativeLower};
use serde_json::json;
use sha2::{Digest, Sha256};
use uuid::Uuid;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let output = PathBuf::from(std::env::args().nth(1).ok_or("new output directory required")?);
    std::fs::create_dir(&output)?;
    let volume = Uuid::parse_str("cc3bd2e3-b6dd-45a6-ae24-73761526414b")?;
    let mut rows = BTreeMap::new();
    for i in 0..32769_u64 { rows.insert(format!("row/{i}"), [0_u8;32]); }
    let bytes = serde_json::to_vec(&json!({"format":"entity-native-lower-v1", "volume":volume,
        "chunk_size":4194304, "max_ino":1, "rows":rows}))?;
    if bytes.len() > 4*1024*1024 { return Err("manifest must stay below byte quota".into()); }
    let hash = hex::encode(Sha256::digest(&bytes));
    std::fs::write(output.join(&hash), &bytes)?;
    let result = tokio::runtime::Builder::new_current_thread().enable_all().build()?.block_on(async {
        NativeLower::open(Arc::new(MemoryEntityBackend::new(0)),
                         Arc::new(LocalFsBackend::new(&output)), volume, &hash).await
    });
    let error = match result { Ok(_) => return Err("oversized native lower unexpectedly accepted".into()), Err(e) => format!("{e:?}") };
    if !error.contains("invalid native manifest") { return Err(format!("unexpected rejection: {error}").into()); }
    let report = json!({"passed":true,"input_rows":32769,"manifest_bytes":bytes.len(),
        "manifest_sha256":hash,"error":error,"redis_used":false,"s3_used":false,
        "scope":"native lower row quota rejection; not million-file performance"});
    std::fs::write(output.join("result.json"), serde_json::to_vec_pretty(&report)?)?;
    println!("{report}");
    Ok(())
}
