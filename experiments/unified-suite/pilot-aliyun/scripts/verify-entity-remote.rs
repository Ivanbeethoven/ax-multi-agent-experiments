//! Experiment-only reader of the ORIGINAL stopped entity namespace.
//! Use a Redis account that denies all metadata mutations. No mount or local spool.
#[path = "verify-object-cache.rs"]
mod verify_object_cache;
use anyhow::{Context, Result, bail, ensure};
use brewfs::{
    cadapter::{
        client::{ObjectBackend, ObjectClient},
        s3::{S3Backend, S3Config},
    },
    workspace_overlay::{
        entity::{
            backend::EntityBackend,
            codec,
            data::ObjectDescriptor,
            keys::EntityKeys,
            lifecycle::{
                Lifecycle, LowerOwner, LowerRegistry, NamespaceDescriptor, NamespaceState,
            },
            model::{ChunkMap, ChunkSource, EntityDentry, EntityKind, UpperInode},
            native_lower::NativeLower,
            ownership,
            packed_lower::PackedLower,
            redis::RedisEntityBackend,
            resolver::{self, ImmutableLower, ImmutableLowerData},
        },
        stores::{kv_store::KvWorkspaceStore, redis::RedisWorkspaceBackend},
    },
};
use futures_util::StreamExt;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    env,
    fs::{self, File, OpenOptions},
    io::{BufWriter, Write},
    path::Path,
    sync::Arc,
};
use uuid::Uuid;
use verify_object_cache::VerificationObjects;

fn new_file(path: &Path) -> Result<BufWriter<File>> {
    Ok(BufWriter::new(
        OpenOptions::new().write(true).create_new(true).open(path)?,
    ))
}
fn line(out: &mut impl Write, row: Value) -> Result<()> {
    serde_json::to_writer(&mut *out, &row)?;
    out.write_all(b"\n")?;
    Ok(())
}
async fn row<B: EntityBackend>(backend: &B, key: Vec<u8>) -> Result<Option<Vec<u8>>> {
    let mut rows = backend.read(&[key]).await?.rows;
    ensure!(rows.len() == 1, "short metadata response");
    Ok(rows.remove(0))
}
async fn required<B: EntityBackend>(backend: &B, key: Vec<u8>) -> Result<Vec<u8>> {
    row(backend, key)
        .await?
        .context("required metadata row absent")
}
async fn stopped<B: EntityBackend>(
    backend: &B,
    keys: &EntityKeys,
) -> Result<(NamespaceDescriptor, Vec<Option<Vec<u8>>>)> {
    let descriptor: NamespaceDescriptor =
        codec::decode(&required(backend, keys.descriptor()).await?)?;
    ensure!(
        descriptor.namespace_id == keys.namespace_id()
            && descriptor.state == NamespaceState::Quiescent
            && descriptor.active_lease.is_none(),
        "original writer must be normally stopped"
    );
    let authority = vec![
        keys.descriptor(),
        keys.lease(),
        keys.remote_pending(),
        keys.maintenance(),
        keys.lower_pin(),
        keys.lower_owner(&descriptor.lower.manifest_digest, keys.namespace_id()),
        keys.lower_registry(&descriptor.lower.manifest_digest),
    ];
    let snapshot = backend.read(&authority).await?;
    ensure!(
        snapshot.rows.len() == authority.len(),
        "short authority snapshot"
    );
    ensure!(
        snapshot.rows[0].as_deref() == Some(codec::encode(&descriptor)?.as_slice()),
        "descriptor changed during admission"
    );
    ensure!(
        snapshot.rows[1].is_none() && snapshot.rows[2].is_none() && snapshot.rows[3].is_some(),
        "lease/debt/maintenance not closed"
    );
    let owner = LowerOwner {
        namespace_id: keys.namespace_id(),
        binding: descriptor.lower.clone(),
    };
    for index in [4, 5] {
        ensure!(
            codec::decode::<LowerOwner>(
                snapshot.rows[index]
                    .as_deref()
                    .context("missing lower pin/owner")?
            )? == owner,
            "lower owner mismatch"
        );
    }
    let registry: LowerRegistry = codec::decode(
        snapshot.rows[6]
            .as_deref()
            .context("missing lower registry")?,
    )?;
    ensure!(
        registry.binding == descriptor.lower && registry.owners > 0,
        "lower registry mismatch"
    );
    Ok((descriptor, snapshot.rows))
}
async fn scan<B: EntityBackend>(
    backend: &B,
    prefix: &[u8],
    mut output: Option<&mut BufWriter<File>>,
) -> Result<(u64, String)> {
    let mut after: Option<Vec<u8>> = None;
    let mut count = 0u64;
    let mut hash = Sha256::new();
    loop {
        let page = backend
            .scan_prefix_page(prefix, after.as_deref(), 128)
            .await?;
        let length = page.len();
        ensure!(length <= 128, "oversized scan page");
        for (key, value) in page {
            ensure!(
                key.starts_with(prefix) && after.as_ref().is_none_or(|a| key > *a),
                "scan cursor did not advance"
            );
            hash.update((key.len() as u64).to_le_bytes());
            hash.update(&key);
            hash.update((value.len() as u64).to_le_bytes());
            hash.update(&value);
            if let Some(out) = output.as_deref_mut() {
                line(
                    out,
                    json!({"key_hex":hex::encode(&key),"value_hex":hex::encode(&value)}),
                )?;
            }
            after = Some(key);
            count += 1;
        }
        if length < 128 {
            break;
        }
    }
    Ok((count, hex::encode(hash.finalize())))
}
async fn directory<B: EntityBackend>(
    backend: &B,
    keys: &EntityKeys,
    ino: i64,
) -> Result<Vec<(Vec<u8>, EntityDentry)>> {
    let prefix = keys.dentry_prefix(ino);
    let mut after: Option<Vec<u8>> = None;
    let mut result = Vec::new();
    loop {
        let page = backend
            .scan_prefix_page(&prefix, after.as_deref(), 128)
            .await?;
        let length = page.len();
        ensure!(length <= 128, "oversized directory page");
        for (key, value) in page {
            ensure!(
                key.starts_with(&prefix) && after.as_ref().is_none_or(|a| key > *a),
                "invalid directory cursor"
            );
            let name = hex::decode(&key[prefix.len()..])?;
            ensure!(key == keys.dentry(ino, &name), "noncanonical dentry key");
            result.push((name, codec::decode(&value)?));
            after = Some(key);
        }
        if length < 128 {
            break;
        }
    }
    Ok(result)
}
async fn object<B: EntityBackend, O: ObjectBackend>(
    backend: &B,
    objects: &O,
    keys: &EntityKeys,
    id: u64,
    out: &mut BufWriter<File>,
) -> Result<Vec<u8>> {
    let descriptor: ObjectDescriptor =
        codec::decode(&required(backend, keys.object_descriptor(id)).await?)?;
    let ownership: ownership::SliceOwnership =
        codec::decode(&required(backend, keys.slice(id)).await?)?;
    ensure!(
        descriptor.namespace_id == keys.namespace_id()
            && descriptor.slice_id == id
            && descriptor.size > 0
            && descriptor.size <= 16 * 1024 * 1024,
        "invalid upper object descriptor"
    );
    ensure!(
        ownership.namespace_id == keys.namespace_id()
            && ownership.slice_id == id
            && ownership.size == descriptor.size
            && ownership.state == ownership::SliceState::Available
            && ownership.current_refs > 0,
        "object descriptor/ownership mismatch"
    );
    let key = keys.object(id);
    let mut data = Vec::with_capacity(descriptor.size as usize);
    let mut stream = objects
        .get_object_range_stream(
            &key,
            0,
            descriptor
                .size
                .checked_add(1)
                .context("object length overflow")?,
        )
        .await?;
    while let Some(part) = stream.next().await {
        let part = part?;
        ensure!(
            data.len()
                .checked_add(part.len())
                .is_some_and(|n| n <= descriptor.size as usize),
            "oversized remote object"
        );
        data.extend_from_slice(&part);
    }
    let digest: [u8; 32] = Sha256::digest(&data).into();
    ensure!(
        data.len() == descriptor.size as usize && digest == descriptor.sha256,
        "remote upper object size/digest mismatch"
    );
    line(
        out,
        json!({"key":key,"bytes":data.len(),"sha256":hex::encode(digest),"verified":true}),
    )?;
    Ok(data)
}
async fn verify<B: EntityBackend, L: ImmutableLowerData + ?Sized, O: ObjectBackend>(
    backend: &B,
    lower: &L,
    objects: &O,
    keys: &EntityKeys,
    output: &Path,
    expected_path: Option<&str>,
    expected_links_path: Option<&str>,
    subtree: &str,
) -> Result<Value> {
    let (descriptor, authority) = stopped(backend, keys).await?;
    ensure!(
        lower.binding() == &descriptor.lower,
        "lower binding mismatch"
    );
    let mut raw = new_file(&output.join("upper-rows.jsonl"))?;
    let before = scan(backend, keys.prefix(), Some(&mut raw)).await?;
    raw.flush()?;
    let object_prefix = [keys.volume_prefix(), b"object/"].concat();
    let slice_prefix = [keys.volume_prefix(), b"slice/"].concat();
    let mut volume_rows = new_file(&output.join("volume-rows.jsonl"))?;
    let objects_before = scan(backend, &object_prefix, Some(&mut volume_rows)).await?;
    let slices_before = scan(backend, &slice_prefix, Some(&mut volume_rows)).await?;
    volume_rows.flush()?;
    let geometry: Value = serde_json::from_slice(
        &required(backend, [keys.prefix(), b"data-config"].concat()).await?,
    )?;
    let chunk_size = geometry["chunk_size"]
        .as_u64()
        .context("missing chunk size")?;
    ensure!(
        chunk_size > 0 && chunk_size <= 16 * 1024 * 1024,
        "unsupported chunk size"
    );
    let mut expected: Option<BTreeMap<String, Value>> = expected_path
        .map(|p| -> Result<_> { Ok(serde_json::from_reader(File::open(p)?)?) })
        .transpose()?;
    let mut tree = new_file(&output.join("tree.jsonl"))?;
    let mut remote = new_file(&output.join("remote-objects.jsonl"))?;
    let mut root_ino = descriptor.lower.root_ino;
    if !subtree.is_empty() {
        for name in subtree.as_bytes().split(|b| *b == b'/') {
            brewfs::workspace_overlay::entity::model::validate_name(name)?;
            let upper: Option<EntityDentry> = row(backend, keys.dentry(root_ino, name))
                .await?
                .map(|b| codec::decode(&b))
                .transpose()?;
            match resolver::resolve_dentry(lower, &descriptor.lower, root_ino, name, upper.as_ref())
                .await?
            {
                Some(EntityDentry::Present {
                    ino,
                    kind: EntityKind::Directory,
                }) => root_ino = ino,
                _ => bail!("verification subtree is not a directory"),
            }
        }
    }
    let mut todo = vec![(Vec::<u8>::new(), root_ino, EntityKind::Directory)];
    let mut directories = BTreeSet::new();
    let mut links: BTreeMap<i64, Vec<Vec<u8>>> = BTreeMap::new();
    let mut files = 0u64;
    let mut logical_bytes = 0u64;
    let mut entries = 0u64;
    // A bounded cache contains only bytes fetched and authenticated HERE from S3.
    let mut cache: BTreeMap<u64, Vec<u8>> = BTreeMap::new();
    let mut cache_bytes = 0usize;
    while let Some((path, ino, kind)) = todo.pop() {
        let upper: Option<UpperInode> = row(backend, keys.inode(ino))
            .await?
            .map(|b| codec::decode(&b))
            .transpose()?;
        let inode = resolver::resolve_inode(lower, &descriptor.lower, ino, upper.as_ref())
            .await?
            .context("dentry points to absent inode")?;
        ensure!(inode.kind == kind, "dentry/inode type mismatch");
        let mut value = match inode.kind {
            EntityKind::Directory => {
                ensure!(directories.insert(ino), "directory cycle or hardlink");
                let upper = directory(backend, keys, ino).await?;
                for (name, child) in
                    resolver::merge_directory(lower, &descriptor.lower, ino, &upper).await?
                {
                    if let EntityDentry::Present { ino, kind } = child {
                        let mut next = path.clone();
                        if !next.is_empty() {
                            next.push(b'/');
                        }
                        next.extend_from_slice(&name);
                        todo.push((next, ino, kind));
                    } else {
                        bail!("unresolved whiteout");
                    }
                }
                json!({"type":"directory"})
            }
            EntityKind::Symlink => {
                json!({"type":"link","target":String::from_utf8(inode.symlink_target.clone().context("missing symlink target")?)?})
            }
            EntityKind::File => {
                let mut file_hash = Sha256::new();
                let mut offset = 0u64;
                while offset < inode.size {
                    let index = offset / chunk_size;
                    let upper: Option<ChunkMap> = row(backend, keys.chunk(ino, index))
                        .await?
                        .map(|b| codec::decode(&b))
                        .transpose()?;
                    let map = resolver::resolve_chunk(
                        lower,
                        &descriptor.lower,
                        &inode,
                        index,
                        chunk_size,
                        upper.as_ref(),
                    )
                    .await?;
                    let plan =
                        ownership::plan_chunk_membership(backend, keys, ino, index, &map, &map)
                            .await?;
                    ensure!(
                        plan.writes.is_empty(),
                        "read-only ownership check generated writes"
                    );
                    let length = chunk_size.min(inode.size - offset);
                    for span in map.clip(0, length)? {
                        match span.source {
                            ChunkSource::Hole => {
                                let zeros = [0u8; 65536];
                                let mut left = span.length;
                                while left > 0 {
                                    let n = left.min(zeros.len() as u64) as usize;
                                    file_hash.update(&zeros[..n]);
                                    left -= n as u64;
                                }
                            }
                            ChunkSource::LowerRange {
                                binding_digest,
                                ino,
                                file_offset,
                            } => {
                                ensure!(
                                    binding_digest == descriptor.lower.manifest_digest,
                                    "foreign lower range"
                                );
                                let data = lower
                                    .read_range(ino, file_offset, span.length as usize)
                                    .await?;
                                ensure!(data.len() == span.length as usize, "short lower read");
                                file_hash.update(&data);
                            }
                            ChunkSource::Data {
                                slice_id,
                                slice_offset,
                            } => {
                                if !cache.contains_key(&slice_id) {
                                    let data =
                                        object(backend, objects, keys, slice_id, &mut remote)
                                            .await?;
                                    if cache_bytes + data.len() > 64 * 1024 * 1024 {
                                        cache.clear();
                                        cache_bytes = 0;
                                    }
                                    cache_bytes += data.len();
                                    cache.insert(slice_id, data);
                                }
                                let data = &cache[&slice_id];
                                let end = slice_offset
                                    .checked_add(span.length)
                                    .context("span overflow")?;
                                ensure!(end <= data.len() as u64, "span exceeds remote object");
                                file_hash.update(&data[slice_offset as usize..end as usize]);
                            }
                        }
                    }
                    offset += length;
                }
                files += 1;
                logical_bytes += inode.size;
                links.entry(ino).or_default().push(path.clone());
                json!({"type":"file","bytes":inode.size,"sha256":hex::encode(file_hash.finalize())})
            }
            _ => bail!("unsupported inode type {:?}", inode.kind),
        };
        if path.is_empty() {
            continue;
        }
        entries += 1;
        value["mode"] = json!(inode.mode);
        // Preserve bytes in evidence. Current fixture/tree oracle uses UTF-8 names.
        let text = std::str::from_utf8(&path).context("tree oracle needs UTF-8 names")?;
        if let Some(expected) = expected.as_mut() {
            ensure!(
                expected.remove(text).as_ref() == Some(&value),
                "logical tree mismatch at {text}"
            );
        }
        line(
            &mut tree,
            json!({"path":text,"path_hex":hex::encode(path),"ino":ino,"value":value}),
        )?;
    }
    if let Some(expected) = expected.as_ref() {
        ensure!(
            expected.is_empty(),
            "expected paths missing from original namespace"
        );
    }
    tree.flush()?;
    remote.flush()?;
    let mut hardlinks = new_file(&output.join("hardlinks.jsonl"))?;
    let mut actual_groups = Vec::<Vec<String>>::new();
    for (ino, paths) in links.iter().filter(|(_, p)| p.len() > 1) {
        line(
            &mut hardlinks,
            json!({"ino":ino,"paths_hex":paths.iter().map(hex::encode).collect::<Vec<_>>()}),
        )?;
        let mut paths = paths
            .iter()
            .map(|p| String::from_utf8(p.clone()))
            .collect::<std::result::Result<Vec<_>, _>>()?;
        paths.sort();
        actual_groups.push(paths);
    }
    actual_groups.sort();
    if let Some(path) = expected_links_path {
        let value: Value = serde_json::from_reader(File::open(path)?)?;
        let mut groups: Vec<Vec<String>> = serde_json::from_value(value["groups"].clone())?;
        for group in &mut groups {
            ensure!(group.len() > 1, "invalid expected hardlink group");
            group.sort();
        }
        groups.sort();
        ensure!(
            actual_groups == groups,
            "hardlink path groups differ from independent oracle"
        );
    }
    hardlinks.flush()?;
    let after = scan(backend, keys.prefix(), None).await?;
    let objects_after = scan(backend, &object_prefix, None).await?;
    let slices_after = scan(backend, &slice_prefix, None).await?;
    let (_, final_authority) = stopped(backend, keys).await?;
    ensure!(
        before == after && authority == final_authority,
        "original namespace or authority changed during verification"
    );
    ensure!(
        objects_before == objects_after && slices_before == slices_after,
        "volume object/slice rows changed during verification"
    );
    Ok(
        json!({"passed":true,"namespace":keys.namespace_id(),"volume":keys.volume_id(),"entries":entries,"files":files,"logical_file_bytes":logical_bytes,
        "upper_rows":before.0,"upper_rows_sha256":before.1,"namespace_unchanged":true,"authority_unchanged":true,
        "object_rows":objects_before.0,"object_rows_sha256":objects_before.1,"slice_rows":slices_before.0,"slice_rows_sha256":slices_before.1,
        "volume_rows_unchanged":true,
        "expected_tree_equal":expected.is_some(),"expected_hardlinks_equal":expected_links_path.is_some(),"subtree":subtree,
        "hardlink_groups":actual_groups.len(),"cache_source":"remote S3 authenticated during this verifier only","scope":"original quiescent namespace logical bytes; not timing or session-resume evidence"}),
    )
}

#[tokio::main]
async fn main() -> Result<()> {
    let args: Vec<String> = env::args().collect();
    ensure!(
        args.len() == 10 || args.len() == 11,
        "usage: verify_entity_remote packed|native VOLUME NAMESPACE CATALOG BUCKET ENDPOINT NEW_OUT EXPECTED_TREE_JSON EXPECTED_HARDLINKS_JSON [RELATIVE_SUBTREE]; credentials: AX_VERIFY_REDIS_URL, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY"
    );
    let kind = &args[1];
    ensure!(kind == "packed" || kind == "native", "unknown lower kind");
    let volume = Uuid::parse_str(&args[2])?;
    let namespace = Uuid::parse_str(&args[3])?;
    let catalog = &args[4];
    let output = Path::new(&args[7]);
    fs::create_dir(output)?;
    let result: Result<Value> = async {
        let redis_url = env::var("AX_VERIFY_REDIS_URL")?;
        let backend = Arc::new(RedisEntityBackend::connect(&redis_url, volume).await?);
        let keys = EntityKeys::new(volume, namespace);
        let descriptor = Lifecycle::new(backend.clone(), keys.clone())
            .descriptor()
            .await?;
        let objects = VerificationObjects::new(
            S3Backend::with_static_credentials(
                S3Config {
                    bucket: args[5].clone(),
                    endpoint: Some(args[6].clone()),
                    region: Some("us-east-1".into()),
                    force_path_style: true,
                    ..Default::default()
                },
                env::var("AWS_ACCESS_KEY_ID")?,
                env::var("AWS_SECRET_ACCESS_KEY")?,
            )
            .await?,
        );
        let mut result = if kind == "packed" {
            let catalog = RedisWorkspaceBackend::connect_read_only(&redis_url, catalog).await?;
            let lower = PackedLower::reopen(
                Arc::new(KvWorkspaceStore::new(catalog)),
                ObjectClient::new(objects.clone()),
                volume,
                namespace,
                descriptor.lower.clone(),
            )
            .await?;
            verify(
                backend.as_ref(),
                &lower,
                &objects,
                &keys,
                output,
                args.get(8).map(String::as_str),
                args.get(9).map(String::as_str),
                args.get(10).map(String::as_str).unwrap_or(""),
            )
            .await
        } else {
            let lower = NativeLower::open(
                backend.clone(),
                Arc::new(objects.clone()),
                volume,
                std::str::from_utf8(&descriptor.lower.manifest_key)?,
            )
            .await?;
            verify(
                backend.as_ref(),
                &lower,
                &objects,
                &keys,
                output,
                args.get(8).map(String::as_str),
                args.get(9).map(String::as_str),
                args.get(10).map(String::as_str).unwrap_or(""),
            )
            .await
        }?;
        result["verification_transport"] = objects.stats();
        Ok(result)
    }
    .await;
    let report = match &result {
        Ok(value) => value.clone(),
        Err(error) => json!({"passed":false,"error":format!("{error:#}")}),
    };
    let mut report_file = new_file(&output.join("verify.json"))?;
    line(&mut report_file, report)?;
    report_file.flush()?;
    result.map(|_| ())
}
