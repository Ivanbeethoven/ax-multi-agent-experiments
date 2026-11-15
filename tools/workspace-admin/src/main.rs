//! Administrative workspace helper built against the selected BrewFS checkout.
//! Provides:
//! 1. `init-shared-volume`: initializes a shared-bucket workspace volume (`upper_bucket: None`).
//! 2. `seal-native-base`: flattens and seals a populated Native KV workspace into an immutable BaseRevision
//!    using paginated KV scans and batched row writes (supporting >16,384 files).
//! 3. `fork-shared-bucket`: creates N child workspaces in `shared` (O(1) metadata overlay) or `materialized`
//!    (duplicated namespace/inode/extent metadata rows referencing shared S3 slices, for C2-namespace-materialized-01).

use std::collections::BTreeSet;
use std::time::Instant;

use anyhow::{Context, Result, bail};
use brewfs::workspace::catalog::{CreateVolumeRoot, CreateWorkspace, WorkspaceStore};
use brewfs::workspace::digest::{CanonicalLayerDelta, delta_digest, root_hash};
use brewfs::workspace::ids::{LayerId, WorkspaceId};
use brewfs::workspace::model::{
    AclDelta, BaseRevision, DataExtentDelta, DentryDelta, ExtentKind, InodeDelta, InodeState,
    LayerRecord, LayerState, ValueOp, WORKSPACE_SCHEMA_VERSION, WorkspaceRecord, WorkspaceState,
    XattrDelta,
};
use brewfs::workspace::resolver::{
    resolve_acl, resolve_dentry, resolve_extents, resolve_inode, resolve_xattr,
};
use brewfs::workspace::stores::kv_backend::{KvCheck, KvReadLimits, KvWrite, WorkspaceKvBackend};
use brewfs::workspace::stores::kv_store::KvWorkspaceStore;
use brewfs::workspace::stores::redis::RedisWorkspaceBackend;
use brewfs::workspace::stores::tikv::TiKvWorkspaceBackend;
use clap::{Parser, Subcommand};
use serde::{Serialize, de::DeserializeOwned};

const ENVELOPE_MAGIC: &[u8; 8] = b"BWSKV001";
const WORKSPACE_BUCKET_MAGIC: &[u8; 8] = b"BWSWR002";
const WRITE_BATCH_ROWS: usize = 256;

#[derive(Parser)]
#[command(name = "ax-brewfs-admin")]
struct Cli {
    #[arg(long, default_value = "redis")]
    meta_backend: String,
    #[arg(long, default_value = "redis://127.0.0.1:6379/0")]
    meta_url: String,
    #[arg(long, value_delimiter = ',')]
    meta_tikv_pd_endpoints: Vec<String>,
    #[arg(long, default_value = "brewfs")]
    workspace_namespace: String,
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    InitSharedVolume {
        #[arg(long)]
        owner: Option<String>,
    },
    SealNativeBase {
        #[arg(long)]
        workspace: String,
    },
    ForkSharedBucket {
        #[arg(long)]
        revision: String,
        #[arg(long, default_value_t = 1)]
        count: usize,
        #[arg(long, default_value = "shared")]
        mode: String,
        #[arg(long)]
        owner: Option<String>,
    },
}

fn encode_kv<T: Serialize>(value: &T) -> Result<Vec<u8>> {
    let payload = bincode::serialize(value)?;
    let mut out = Vec::with_capacity(ENVELOPE_MAGIC.len() + payload.len());
    out.extend_from_slice(ENVELOPE_MAGIC);
    out.extend_from_slice(&payload);
    Ok(out)
}

fn encode_workspace(value: &WorkspaceRecord) -> Result<Vec<u8>> {
    let payload = bincode::serialize(value)?;
    let mut out = Vec::with_capacity(WORKSPACE_BUCKET_MAGIC.len() + payload.len());
    out.extend_from_slice(WORKSPACE_BUCKET_MAGIC);
    out.extend_from_slice(&payload);
    Ok(out)
}

fn decode_kv<T: DeserializeOwned>(bytes: &[u8]) -> Result<T> {
    let payload = bytes
        .strip_prefix(ENVELOPE_MAGIC)
        .context("missing BWSKV001 envelope")?;
    Ok(bincode::deserialize(payload)?)
}

fn decode_workspace(bytes: &[u8]) -> Result<WorkspaceRecord> {
    if let Some(payload) = bytes.strip_prefix(WORKSPACE_BUCKET_MAGIC) {
        return Ok(bincode::deserialize(payload)?);
    }
    let payload = bytes
        .strip_prefix(ENVELOPE_MAGIC)
        .context("missing workspace envelope")?;
    let mut upgraded = payload.to_vec();
    upgraded.push(0);
    Ok(bincode::deserialize(&upgraded)?)
}

fn ino_component(ino: i64) -> String {
    format!("{:016x}", (ino as u64) ^ (1_u64 << 63))
}

fn u64_component(value: u64) -> String {
    format!("{value:016x}")
}

fn dentry_key(row: &DentryDelta) -> Vec<u8> {
    format!(
        "delta/dentry/{}/{}/{}",
        row.layer_id,
        ino_component(row.parent_ino),
        hex::encode(&row.name)
    )
    .into_bytes()
}

fn inode_key(row: &InodeDelta) -> Vec<u8> {
    format!("delta/inode/{}/{}", row.layer_id, ino_component(row.ino)).into_bytes()
}

fn xattr_key(row: &XattrDelta) -> Vec<u8> {
    format!(
        "delta/xattr/{}/{}/{}",
        row.layer_id,
        ino_component(row.ino),
        hex::encode(&row.name)
    )
    .into_bytes()
}

fn acl_key(row: &AclDelta) -> Vec<u8> {
    format!(
        "delta/acl/{}/{}/{:02x}/{}",
        row.layer_id,
        ino_component(row.ino),
        row.acl_type,
        ino_component(row.acl_id)
    )
    .into_bytes()
}

fn extent_key(row: &DataExtentDelta) -> Vec<u8> {
    format!(
        "delta/extent/{}/{}/{}/{}",
        row.layer_id,
        ino_component(row.ino),
        u64_component(row.chunk_index),
        u64_component(row.sequence)
    )
    .into_bytes()
}

fn workspace_key(id: WorkspaceId) -> Vec<u8> {
    format!("ws/{id}").into_bytes()
}

fn layer_key(id: LayerId) -> Vec<u8> {
    format!("layer/{id}").into_bytes()
}

fn allocator_key(name: &str) -> Vec<u8> {
    format!("alloc/{name}").into_bytes()
}

async fn scan_all_decoded<T: DeserializeOwned>(
    backend: &(impl WorkspaceKvBackend + Clone),
    prefix: &[u8],
) -> Result<Vec<T>> {
    let limits = KvReadLimits {
        max_records: 512,
        max_key_bytes: 1024,
        max_value_bytes: 256 << 10,
        max_total_bytes: 16 << 20,
        max_response_bytes: 16 << 20,
        max_data_requests: 64,
    };
    let mut out = Vec::new();
    let mut cursor: Option<Vec<u8>> = None;
    loop {
        let page = backend
            .scan_prefix_page_with_byte_limits(prefix, cursor.as_deref(), limits)
            .await?;
        if page.is_empty() {
            break;
        }
        cursor = page.last().map(|e| e.key.clone());
        for entry in page {
            out.push(decode_kv::<T>(&entry.value)?);
        }
    }
    Ok(out)
}

async fn load_unlimited_layer_delta(
    backend: &(impl WorkspaceKvBackend + Clone),
    layer_id: LayerId,
) -> Result<CanonicalLayerDelta> {
    let dentries = scan_all_decoded(
        backend,
        format!("delta/dentry/{layer_id}/").as_bytes(),
    )
    .await?;
    let inodes = scan_all_decoded(
        backend,
        format!("delta/inode/{layer_id}/").as_bytes(),
    )
    .await?;
    let xattrs = scan_all_decoded(
        backend,
        format!("delta/xattr/{layer_id}/").as_bytes(),
    )
    .await?;
    let acls = scan_all_decoded(
        backend,
        format!("delta/acl/{layer_id}/").as_bytes(),
    )
    .await?;
    let extents = scan_all_decoded(
        backend,
        format!("delta/extent/{layer_id}/").as_bytes(),
    )
    .await?;
    Ok(CanonicalLayerDelta {
        dentries,
        inodes,
        xattrs,
        acls,
        extents,
    })
}

fn build_delta_writes(delta: &CanonicalLayerDelta) -> Result<(Vec<KvWrite>, usize)> {
    let mut writes = Vec::with_capacity(
        delta.dentries.len()
            + delta.inodes.len()
            + delta.xattrs.len()
            + delta.acls.len()
            + delta.extents.len(),
    );
    let mut bytes = 0usize;
    for row in &delta.dentries {
        let key = dentry_key(row);
        let value = encode_kv(row)?;
        bytes += key.len() + value.len();
        writes.push(KvWrite::Put { key, value });
    }
    for row in &delta.inodes {
        let key = inode_key(row);
        let value = encode_kv(row)?;
        bytes += key.len() + value.len();
        writes.push(KvWrite::Put { key, value });
    }
    for row in &delta.xattrs {
        let key = xattr_key(row);
        let value = encode_kv(row)?;
        bytes += key.len() + value.len();
        writes.push(KvWrite::Put { key, value });
    }
    for row in &delta.acls {
        let key = acl_key(row);
        let value = encode_kv(row)?;
        bytes += key.len() + value.len();
        writes.push(KvWrite::Put { key, value });
    }
    for row in &delta.extents {
        let key = extent_key(row);
        let value = encode_kv(row)?;
        bytes += key.len() + value.len();
        writes.push(KvWrite::Put { key, value });
    }
    Ok((writes, bytes))
}

async fn write_batched(backend: &(impl WorkspaceKvBackend + Clone), writes: &[KvWrite]) -> Result<usize> {
    let mut batches = 0usize;
    for chunk in writes.chunks(WRITE_BATCH_ROWS) {
        let ok = backend.compare_and_swap(&[], chunk).await?;
        if !ok {
            bail!("unexpected CAS rejection on unreferenced layer write batch");
        }
        batches += 1;
    }
    Ok(batches)
}

fn rewrite_delta_layer(src: &CanonicalLayerDelta, target_layer: LayerId) -> CanonicalLayerDelta {
    let mut out = CanonicalLayerDelta::default();
    out.dentries = src
        .dentries
        .iter()
        .cloned()
        .map(|mut r| {
            r.layer_id = target_layer;
            r
        })
        .collect();
    out.inodes = src
        .inodes
        .iter()
        .cloned()
        .map(|mut r| {
            r.layer_id = target_layer;
            r
        })
        .collect();
    out.xattrs = src
        .xattrs
        .iter()
        .cloned()
        .map(|mut r| {
            r.layer_id = target_layer;
            r
        })
        .collect();
    out.acls = src
        .acls
        .iter()
        .cloned()
        .map(|mut r| {
            r.layer_id = target_layer;
            r
        })
        .collect();
    out.extents = src
        .extents
        .iter()
        .cloned()
        .map(|mut r| {
            r.layer_id = target_layer;
            r
        })
        .collect();
    out
}

fn max_next_sequence(delta: &CanonicalLayerDelta) -> u64 {
    delta
        .dentries
        .iter()
        .map(|r| r.sequence)
        .chain(delta.inodes.iter().map(|r| r.sequence))
        .chain(delta.xattrs.iter().map(|r| r.sequence))
        .chain(delta.acls.iter().map(|r| r.sequence))
        .chain(delta.extents.iter().map(|r| r.sequence))
        .max()
        .unwrap_or(0)
        .saturating_add(1)
}

fn flatten_chain(output_layer: LayerId, chain: &[LayerRecord], input: &CanonicalLayerDelta) -> Result<CanonicalLayerDelta> {
    let mut output = CanonicalLayerDelta::default();
    let dentry_keys = input
        .dentries
        .iter()
        .map(|row| (row.parent_ino, row.name.clone()))
        .collect::<BTreeSet<_>>();
    for (parent, name) in dentry_keys {
        if let Some(entry) = resolve_dentry(chain, &input.dentries, parent, &name)? {
            output.dentries.push(DentryDelta::put(
                output_layer,
                parent,
                name,
                entry.ino,
                entry.entry_type,
                0,
            ));
        }
    }
    let inode_keys = input.inodes.iter().map(|row| row.ino).collect::<BTreeSet<_>>();
    for ino in inode_keys {
        if let Some(mut inode) = resolve_inode(chain, &input.inodes, ino)?.map(|v| v.inode) {
            inode.layer_id = output_layer;
            inode.state = InodeState::Present;
            inode.sequence = 0;
            output.inodes.push(inode);
        }
    }
    let xattr_keys = input
        .xattrs
        .iter()
        .map(|row| (row.ino, row.name.clone()))
        .collect::<BTreeSet<_>>();
    for (ino, name) in xattr_keys {
        if let Some(value) = resolve_xattr(chain, &input.xattrs, ino, &name)? {
            output.xattrs.push(XattrDelta {
                layer_id: output_layer,
                ino,
                name,
                op: ValueOp::Put,
                value: Some(value.value),
                sequence: 0,
            });
        }
    }
    let acl_keys = input
        .acls
        .iter()
        .map(|row| (row.ino, row.acl_type, row.acl_id))
        .collect::<BTreeSet<_>>();
    for (ino, acl_type, acl_id) in acl_keys {
        if let Some(value) = resolve_acl(chain, &input.acls, ino, acl_type, acl_id)? {
            output.acls.push(AclDelta {
                layer_id: output_layer,
                ino,
                acl_type,
                acl_id,
                op: ValueOp::Put,
                value: Some(value.value),
                sequence: 0,
            });
        }
    }
    let extent_keys = input
        .extents
        .iter()
        .map(|row| (row.ino, row.chunk_index))
        .collect::<BTreeSet<_>>();
    for (ino, chunk_index) in extent_keys {
        let range_end = input
            .extents
            .iter()
            .filter(|row| row.ino == ino && row.chunk_index == chunk_index)
            .fold(0u64, |end, row| end.max(row.logical_offset.saturating_add(row.length)));
        for extent in resolve_extents(chain, &input.extents, ino, chunk_index, 0..range_end)? {
            if let ExtentKind::Data {
                slice_id,
                slice_offset,
            } = extent.kind
            {
                output.extents.push(DataExtentDelta::data(
                    output_layer,
                    ino,
                    chunk_index,
                    extent.logical_offset,
                    extent.length,
                    slice_id,
                    slice_offset,
                    0,
                ));
            }
        }
    }
    let mut seq = 1u64;
    for row in &mut output.dentries {
        row.sequence = seq;
        seq += 1;
    }
    for row in &mut output.inodes {
        row.sequence = seq;
        seq += 1;
    }
    for row in &mut output.xattrs {
        row.sequence = seq;
        seq += 1;
    }
    for row in &mut output.acls {
        row.sequence = seq;
        seq += 1;
    }
    for row in &mut output.extents {
        row.sequence = seq;
        seq += 1;
    }
    Ok(output)
}

#[tokio::main]
async fn main() -> Result<()> {
    let cli = Cli::parse();
    if cli.meta_backend == "tikv" {
        let backend = TiKvWorkspaceBackend::connect(cli.meta_tikv_pd_endpoints.clone(), &cli.workspace_namespace).await?;
        execute(cli.command, backend).await
    } else if cli.meta_backend == "redis" {
        let backend = RedisWorkspaceBackend::connect(&cli.meta_url, &cli.workspace_namespace).await?;
        execute(cli.command, backend).await
    } else {
        bail!("unsupported metadata backend: {}", cli.meta_backend)
    }
}

async fn execute(command: Command, backend: impl WorkspaceKvBackend + Clone + 'static) -> Result<()> {
    let started = Instant::now();
    let store = KvWorkspaceStore::new(backend.clone());

    match command {
        Command::InitSharedVolume { owner } => {
            if store.load_volume_header().await?.is_some() {
                bail!("workspace volume is already initialized");
            }
            store.initialize_workspace_schema().await?;
            let workspace_id = WorkspaceId::new();
            let ws = store
                .create_volume_root(CreateVolumeRoot {
                    upper_bucket: None,
                    volume_format: "workspace".into(),
                    schema_version: WORKSPACE_SCHEMA_VERSION,
                    volume_id: uuid::Uuid::now_v7(),
                    workspace_id,
                    root_layer_id: LayerId::new(),
                    writable_layer_id: LayerId::new(),
                    owner_id: owner,
                })
                .await?;
            let out = serde_json::json!({
                "workspace_id": ws.workspace_id.to_string(),
                "head_layer_id": ws.head_layer_id.to_string(),
                "fork_base": ws.fork_base.map(|r| r.to_string()),
                "upper_bucket": ws.upper_bucket,
                "wall_seconds": started.elapsed().as_secs_f64(),
            });
            println!("{}", serde_json::to_string_pretty(&out)?);
        }
        Command::SealNativeBase { workspace } => {
            let ws_id: WorkspaceId = workspace.parse()?;
            let ws_record = store.load_workspace(ws_id).await?;
            if ws_record.active_lease.is_some() {
                bail!("unmount workspace before sealing native base (active_lease present)");
            }
            let chain = store.load_layer_chain(ws_record.head_layer_id).await?;
            if chain.len() < 2 {
                bail!("expected writable head over parent layer");
            }
            let expected_head = chain[0].clone();
            let expected_parent_id = expected_head
                .parent_layer_id
                .context("writable head missing parent_layer_id")?;

            let mut combined = CanonicalLayerDelta::default();
            for layer in &chain {
                let d = load_unlimited_layer_delta(&backend, layer.layer_id).await?;
                combined.dentries.extend(d.dentries);
                combined.inodes.extend(d.inodes);
                combined.xattrs.extend(d.xattrs);
                combined.acls.extend(d.acls);
                combined.extents.extend(d.extents);
            }

            let compacted_layer_id = LayerId::new();
            let replacement_head_id = LayerId::new();
            let flattened = flatten_chain(compacted_layer_id, &chain, &combined)?;
            let digest = delta_digest(&flattened)?;
            let root = root_hash([0; 32], digest);
            let next_seq = max_next_sequence(&flattened);

            let (delta_writes, metadata_bytes) = build_delta_writes(&flattened)?;
            let total_rows = delta_writes.len();
            let redis_batches = write_batched(&backend, &delta_writes).await?;

            // Commit topology transition atomically via CAS.
            let ws_k = workspace_key(ws_id);
            let head_k = layer_key(expected_head.layer_id);
            let parent_k = layer_key(expected_parent_id);
            let comp_k = layer_key(compacted_layer_id);
            let repl_k = layer_key(replacement_head_id);
            let alloc_k = allocator_key("sealed_version");

            let keys = vec![
                ws_k.clone(),
                head_k.clone(),
                parent_k.clone(),
                comp_k.clone(),
                repl_k.clone(),
                alloc_k.clone(),
            ];
            let vals = backend.get_many_consistent(&keys).await?;
            let cur_ws = decode_workspace(vals[0].as_deref().context("workspace missing")?)?;
            if cur_ws.head_layer_id != ws_record.head_layer_id
                || cur_ws.head_epoch != ws_record.head_epoch
                || cur_ws.state != WorkspaceState::Active
            {
                bail!("workspace changed concurrently during seal");
            }
            let mut old_head: LayerRecord =
                decode_kv(vals[1].as_deref().context("head layer missing")?)?;
            let _parent_rec: LayerRecord =
                decode_kv(vals[2].as_deref().context("parent layer missing")?)?;
            if vals[3].is_some() || vals[4].is_some() {
                bail!("fresh layer UUID collision");
            }
            let raw_sv: i64 = decode_kv(vals[5].as_deref().context("sealed_version missing")?)?;
            let sealed_version = u64::try_from(raw_sv)?;
            let now = backend.server_time_ns().await?;

            let compacted_rec = LayerRecord {
                layer_id: compacted_layer_id,
                parent_layer_id: None,
                state: LayerState::Sealed,
                schema_version: WORKSPACE_SCHEMA_VERSION,
                sealed_version: Some(sealed_version),
                delta_digest: Some(digest),
                root_hash: Some(root),
                depth: 1,
                owner_workspace_id: None,
                next_sequence: next_seq,
                owned_slice_count: 0,
                owned_bytes: 0,
                created_at_ns: now,
                sealed_at_ns: Some(now),
            };
            let repl_rec = LayerRecord {
                layer_id: replacement_head_id,
                parent_layer_id: Some(compacted_layer_id),
                state: LayerState::Writable,
                schema_version: WORKSPACE_SCHEMA_VERSION,
                sealed_version: None,
                delta_digest: None,
                root_hash: None,
                depth: 2,
                owner_workspace_id: Some(ws_id),
                next_sequence: 1,
                owned_slice_count: 0,
                owned_bytes: 0,
                created_at_ns: now,
                sealed_at_ns: None,
            };
            old_head.state = LayerState::Deleting;
            old_head.owner_workspace_id = None;

            let revision = BaseRevision {
                layer_id: compacted_layer_id,
                sealed_version,
                root_hash: root,
            };
            let mut next_ws = cur_ws;
            next_ws.head_layer_id = replacement_head_id;
            next_ws.head_epoch += 1;
            next_ws.fork_base = Some(revision.clone());
            next_ws.updated_at_ns = now;

            let checks: Vec<KvCheck> = keys
                .into_iter()
                .zip(vals.into_iter())
                .map(|(key, expected)| KvCheck { key, expected })
                .collect();
            let writes = vec![
                KvWrite::Put {
                    key: ws_k,
                    value: encode_workspace(&next_ws)?,
                },
                KvWrite::Put {
                    key: head_k,
                    value: encode_kv(&old_head)?,
                },
                KvWrite::Put {
                    key: comp_k,
                    value: encode_kv(&compacted_rec)?,
                },
                KvWrite::Put {
                    key: repl_k,
                    value: encode_kv(&repl_rec)?,
                },
                KvWrite::Put {
                    key: alloc_k,
                    value: encode_kv(&(raw_sv + 1))?,
                },
            ];
            let committed = backend.compare_and_swap(&checks, &writes).await?;
            if !committed {
                bail!("CAS conflict committing sealed native base");
            }
            let out = serde_json::json!({
                "workspace_id": ws_id.to_string(),
                "revision": revision.to_string(),
                "compacted_layer_id": compacted_layer_id.to_string(),
                "replacement_head_layer_id": replacement_head_id.to_string(),
                "sealed_version": sealed_version,
                "dentries": flattened.dentries.len(),
                "inodes": flattened.inodes.len(),
                "xattrs": flattened.xattrs.len(),
                "acls": flattened.acls.len(),
                "extents": flattened.extents.len(),
                "total_rows": total_rows,
                "metadata_bytes": metadata_bytes,
                "redis_batches": redis_batches + 1,
                "wall_seconds": started.elapsed().as_secs_f64(),
            });
            println!("{}", serde_json::to_string_pretty(&out)?);
        }
        Command::ForkSharedBucket {
            revision,
            count,
            mode,
            owner,
        } => {
            if count == 0 {
                bail!("count must be >= 1");
            }
            let base_rev: BaseRevision = revision.parse()?;
            match mode.as_str() {
                "shared" => {
                    let mut created = Vec::with_capacity(count);
                    let mut created_bytes = 0usize;
                    for _ in 0..count {
                        let ws = store
                            .create_workspace(CreateWorkspace {
                                workspace_id: WorkspaceId::new(),
                                head_layer_id: LayerId::new(),
                                base_revision: base_rev.clone(),
                                owner_id: owner.clone(),
                                upper_bucket: None,
                            })
                            .await?;
                        created_bytes += encode_workspace(&ws)?.len() + 256;
                        created.push(ws.workspace_id.to_string());
                    }
                    let out = serde_json::json!({
                        "mode": "shared",
                        "parent_revision": base_rev.to_string(),
                        "count": count,
                        "workspaces": created,
                        "created_rows": count * 2,
                        "created_bytes": created_bytes,
                        "redis_requests": count,
                        "wall_seconds": started.elapsed().as_secs_f64(),
                    });
                    println!("{}", serde_json::to_string_pretty(&out)?);
                }
                "materialized" => {
                    let src_delta = load_unlimited_layer_delta(&backend, base_rev.layer_id).await?;
                    let next_seq = max_next_sequence(&src_delta);
                    let mut created = Vec::with_capacity(count);
                    let mut created_rows = 0usize;
                    let mut created_bytes = 0usize;
                    let mut redis_batches = 0usize;

                    for _ in 0..count {
                        let child_base_id = LayerId::new();
                        let child_head_id = LayerId::new();
                        let child_ws_id = WorkspaceId::new();

                        let child_delta = rewrite_delta_layer(&src_delta, child_base_id);
                        let digest = delta_digest(&child_delta)?;
                        let root = root_hash([0; 32], digest);
                        let (delta_writes, bytes) = build_delta_writes(&child_delta)?;
                        created_rows += delta_writes.len() + 3;
                        created_bytes += bytes;
                        redis_batches += write_batched(&backend, &delta_writes).await?;

                        let alloc_k = allocator_key("sealed_version");
                        let base_k = layer_key(child_base_id);
                        let head_k = layer_key(child_head_id);
                        let ws_k = workspace_key(child_ws_id);
                        let keys = vec![
                            alloc_k.clone(),
                            base_k.clone(),
                            head_k.clone(),
                            ws_k.clone(),
                        ];
                        let vals = backend.get_many_consistent(&keys).await?;
                        let raw_sv: i64 =
                            decode_kv(vals[0].as_deref().context("sealed_version missing")?)?;
                        let sv = u64::try_from(raw_sv)?;
                        let now = backend.server_time_ns().await?;

                        let base_rec = LayerRecord {
                            layer_id: child_base_id,
                            parent_layer_id: None,
                            state: LayerState::Sealed,
                            schema_version: WORKSPACE_SCHEMA_VERSION,
                            sealed_version: Some(sv),
                            delta_digest: Some(digest),
                            root_hash: Some(root),
                            depth: 1,
                            owner_workspace_id: None,
                            next_sequence: next_seq,
                            owned_slice_count: 0,
                            owned_bytes: 0,
                            created_at_ns: now,
                            sealed_at_ns: Some(now),
                        };
                        let head_rec = LayerRecord {
                            layer_id: child_head_id,
                            parent_layer_id: Some(child_base_id),
                            state: LayerState::Writable,
                            schema_version: WORKSPACE_SCHEMA_VERSION,
                            sealed_version: None,
                            delta_digest: None,
                            root_hash: None,
                            depth: 2,
                            owner_workspace_id: Some(child_ws_id),
                            next_sequence: 1,
                            owned_slice_count: 0,
                            owned_bytes: 0,
                            created_at_ns: now,
                            sealed_at_ns: None,
                        };
                        let child_rev = BaseRevision {
                            layer_id: child_base_id,
                            sealed_version: sv,
                            root_hash: root,
                        };
                        let ws_rec = WorkspaceRecord {
                            workspace_id: child_ws_id,
                            head_layer_id: child_head_id,
                            head_epoch: 1,
                            fork_base: Some(child_rev),
                            owner_id: owner.clone(),
                            state: WorkspaceState::Active,
                            active_lease: None,
                            created_at_ns: now,
                            updated_at_ns: now,
                            upper_bucket: None,
                        };
                        let base_val = encode_kv(&base_rec)?;
                        let head_val = encode_kv(&head_rec)?;
                        let ws_val = encode_workspace(&ws_rec)?;
                        created_bytes += base_val.len() + head_val.len() + ws_val.len();

                        let checks: Vec<KvCheck> = keys
                            .into_iter()
                            .zip(vals.into_iter())
                            .map(|(key, expected)| KvCheck { key, expected })
                            .collect();
                        let writes = vec![
                            KvWrite::Put {
                                key: alloc_k,
                                value: encode_kv(&(raw_sv + 1))?,
                            },
                            KvWrite::Put {
                                key: base_k,
                                value: base_val,
                            },
                            KvWrite::Put {
                                key: head_k,
                                value: head_val,
                            },
                            KvWrite::Put {
                                key: ws_k,
                                value: ws_val,
                            },
                        ];
                        if !backend.compare_and_swap(&checks, &writes).await? {
                            bail!("CAS conflict creating materialized child workspace");
                        }
                        redis_batches += 1;
                        created.push(child_ws_id.to_string());
                    }

                    let out = serde_json::json!({
                        "mode": "materialized",
                        "parent_revision": base_rev.to_string(),
                        "count": count,
                        "workspaces": created,
                        "base_rows_per_child": src_delta.dentries.len() + src_delta.inodes.len() + src_delta.xattrs.len() + src_delta.acls.len() + src_delta.extents.len(),
                        "created_rows": created_rows,
                        "created_bytes": created_bytes,
                        "redis_requests": redis_batches,
                        "wall_seconds": started.elapsed().as_secs_f64(),
                    });
                    println!("{}", serde_json::to_string_pretty(&out)?);
                }
                other => bail!("unsupported mode {other}; expected shared or materialized"),
            }
        }
    }
    Ok(())
}
