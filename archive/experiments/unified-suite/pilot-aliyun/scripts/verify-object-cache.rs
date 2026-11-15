//! Bounded read-only transport for OFF-TIMER verification of immutable objects.
//! Every retained byte originates from this invocation's actual remote GET.
use anyhow::{Result, bail, ensure};
use async_trait::async_trait;
use brewfs::cadapter::client::{ObjectBackend, ObjectByteStream};
use bytes::Bytes;
use futures_util::StreamExt;
use std::{
    collections::BTreeMap,
    sync::{
        Arc, Mutex,
        atomic::{AtomicU64, Ordering},
    },
};

const LIMIT: usize = 64 * 1024 * 1024;
const MAX_ENTRIES: usize = 4096;
type Key = (String, u64, u64);
#[derive(Default)]
struct Cache {
    ranges: BTreeMap<Key, Vec<u8>>,
    bytes: usize,
}

pub struct VerificationObjects<O> {
    inner: Arc<O>,
    cache: Arc<Mutex<Cache>>,
    fetches: Arc<AtomicU64>,
    fetched_bytes: Arc<AtomicU64>,
}
impl<O> Clone for VerificationObjects<O> {
    fn clone(&self) -> Self {
        Self {
            inner: self.inner.clone(),
            cache: self.cache.clone(),
            fetches: self.fetches.clone(),
            fetched_bytes: self.fetched_bytes.clone(),
        }
    }
}
impl<O: ObjectBackend> VerificationObjects<O> {
    pub fn new(inner: O) -> Self {
        Self {
            inner: Arc::new(inner),
            cache: Arc::new(Mutex::new(Cache::default())),
            fetches: Arc::new(AtomicU64::new(0)),
            fetched_bytes: Arc::new(AtomicU64::new(0)),
        }
    }
    pub fn stats(&self) -> serde_json::Value {
        serde_json::json!({"limit_bytes":LIMIT,"cached_bytes":self.cache.lock().unwrap().bytes,"range_fetches":self.fetches.load(Ordering::Relaxed),"range_body_bytes":self.fetched_bytes.load(Ordering::Relaxed),"scope":"verifier-only remote read cache; excluded from performance"})
    }
    async fn range(&self, key: &str, offset: u64, length: u64) -> Result<Vec<u8>> {
        ensure!(
            length <= LIMIT as u64,
            "verification range exceeds buffer bound"
        );
        let identity = (key.to_owned(), offset, length);
        if let Some(data) = self.cache.lock().unwrap().ranges.get(&identity) {
            return Ok(data.clone());
        }
        self.fetches.fetch_add(1, Ordering::Relaxed);
        let mut stream = self
            .inner
            .get_object_range_stream(key, offset, length)
            .await?;
        let mut data = Vec::new();
        while let Some(part) = stream.next().await {
            let part = part?;
            ensure!(
                data.len()
                    .checked_add(part.len())
                    .is_some_and(|n| n <= length as usize),
                "oversized remote range body"
            );
            self.fetched_bytes
                .fetch_add(part.len() as u64, Ordering::Relaxed);
            data.extend_from_slice(&part);
        }
        let mut cache = self.cache.lock().unwrap();
        if cache.bytes + data.len() > LIMIT || cache.ranges.len() >= MAX_ENTRIES {
            cache.ranges.clear();
            cache.bytes = 0;
        }
        if !cache.ranges.contains_key(&identity) {
            cache.bytes += data.len();
            cache.ranges.insert(identity, data.clone());
        }
        Ok(data)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    struct Fake {
        calls: AtomicU64,
        oversized: bool,
    }
    #[async_trait]
    impl ObjectBackend for Fake {
        async fn put_object(&self, _: &str, _: &[u8]) -> Result<()> {
            panic!("must not reach writes")
        }
        async fn delete_object(&self, _: &str) -> Result<()> {
            panic!("must not reach deletes")
        }
        async fn get_object(&self, _: &str) -> Result<Option<Vec<u8>>> {
            Ok(Some(vec![7; 4]))
        }
        async fn get_object_range(&self, _: &str, _: u64, _: &mut [u8]) -> Result<usize> {
            panic!("stream required")
        }
        async fn get_etag(&self, _: &str) -> Result<String> {
            Ok("immutable".into())
        }
        async fn get_object_range_stream(
            &self,
            _: &str,
            _: u64,
            length: u64,
        ) -> Result<ObjectByteStream> {
            self.calls.fetch_add(1, Ordering::Relaxed);
            let length = length + u64::from(self.oversized);
            Ok(Box::pin(futures_util::stream::once(async move {
                Ok(Bytes::from(vec![7; length as usize]))
            })))
        }
    }
    fn reader(oversized: bool) -> VerificationObjects<Fake> {
        VerificationObjects::new(Fake {
            calls: AtomicU64::new(0),
            oversized,
        })
    }
    #[tokio::test]
    async fn caches_exact_range_only_and_refuses_mutations() {
        let reader = reader(false);
        assert_eq!(reader.range("x", 0, 4).await.unwrap(), vec![7; 4]);
        assert_eq!(reader.range("x", 0, 4).await.unwrap(), vec![7; 4]);
        assert_eq!(reader.inner.calls.load(Ordering::Relaxed), 1);
        reader.range("x", 1, 4).await.unwrap();
        reader.range("x", 0, 5).await.unwrap();
        assert_eq!(reader.inner.calls.load(Ordering::Relaxed), 3);
        assert!(reader.put_object("x", b"bad").await.is_err());
        assert!(reader.delete_object("x").await.is_err());
    }
    #[tokio::test]
    async fn rejects_oversized_body_without_caching_partial_success() {
        let reader = reader(true);
        assert!(reader.range("x", 0, 4).await.is_err());
        assert!(reader.cache.lock().unwrap().ranges.is_empty());
    }
    #[tokio::test]
    async fn cache_body_and_entry_counts_are_bounded() {
        let reader = reader(false);
        for i in 0..17 {
            reader
                .range(&i.to_string(), 0, 4 * 1024 * 1024)
                .await
                .unwrap();
        }
        assert!(reader.cache.lock().unwrap().bytes <= LIMIT);
        for i in 0..MAX_ENTRIES + 1 {
            reader.range(&format!("empty-{i}"), 0, 0).await.unwrap();
        }
        assert!(reader.cache.lock().unwrap().ranges.len() <= MAX_ENTRIES);
    }
}
#[async_trait]
impl<O: ObjectBackend + 'static> ObjectBackend for VerificationObjects<O> {
    async fn put_object(&self, _: &str, _: &[u8]) -> Result<()> {
        bail!("verification transport forbids PUT")
    }
    async fn delete_object(&self, _: &str) -> Result<()> {
        bail!("verification transport forbids DELETE")
    }
    async fn get_object(&self, key: &str) -> Result<Option<Vec<u8>>> {
        self.inner.get_object(key).await
    }
    async fn get_object_range(&self, key: &str, offset: u64, buffer: &mut [u8]) -> Result<usize> {
        let data = self.range(key, offset, buffer.len() as u64).await?;
        buffer[..data.len()].copy_from_slice(&data);
        Ok(data.len())
    }
    async fn get_object_range_stream(
        &self,
        key: &str,
        offset: u64,
        length: u64,
    ) -> Result<ObjectByteStream> {
        if length > LIMIT as u64 {
            return self
                .inner
                .get_object_range_stream(key, offset, length)
                .await;
        }
        let data = self.range(key, offset, length).await?;
        Ok(Box::pin(futures_util::stream::once(async move {
            Ok(Bytes::from(data))
        })))
    }
    async fn get_object_size_bounded(&self, key: &str) -> Result<Option<u64>> {
        self.inner.get_object_size_bounded(key).await
    }
    async fn get_etag(&self, key: &str) -> Result<String> {
        self.inner.get_etag(key).await
    }
}
