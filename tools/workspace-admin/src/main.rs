//! Initialize AX volumes and fork private writable heads from registered parent revisions.

use std::time::Instant;

use anyhow::{Result, bail};
use graftfs::workspace::catalog::{CreateVolumeRoot, CreateWorkspace, WorkspaceStore};
use graftfs::workspace::ids::{LayerId, WorkspaceId};
use graftfs::workspace::model::{BaseRevision, WORKSPACE_SCHEMA_VERSION};
use graftfs::workspace::stores::kv_backend::WorkspaceKvBackend;
use graftfs::workspace::stores::kv_store::KvWorkspaceStore;
use graftfs::workspace::stores::redis::RedisWorkspaceBackend;
use graftfs::workspace::stores::tikv::TiKvWorkspaceBackend;
use clap::{Parser, Subcommand};

#[derive(Parser)]
#[command(name = "ax-graftfs-admin")]
struct Cli {
    #[arg(long, default_value = "redis")]
    meta_backend: String,
    #[arg(long, default_value = "redis://127.0.0.1:6379/0")]
    meta_url: String,
    #[arg(long, value_delimiter = ',')]
    meta_tikv_pd_endpoints: Vec<String>,
    #[arg(long, default_value = "graftfs")]
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
    ForkSharedBucket {
        #[arg(long)]
        revision: String,
        #[arg(long, default_value_t = 1)]
        count: usize,
        #[arg(long, default_value = "shared", value_parser = ["shared"])]
        mode: String,
        #[arg(long)]
        owner: Option<String>,
    },
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
    let store = KvWorkspaceStore::new(backend);

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
                created_bytes += bincode::serialized_size(&ws)? as usize + 8 + 256;
                created.push(ws.workspace_id.to_string());
            }
            let out = serde_json::json!({
                "mode": mode,
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
    }
    Ok(())
}
