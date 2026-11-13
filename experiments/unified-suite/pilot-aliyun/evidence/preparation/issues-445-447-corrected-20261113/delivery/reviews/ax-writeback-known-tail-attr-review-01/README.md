# Known-tail attribute transition: read-only review

The archived read wave returns exactly 64 MiB contiguously and EOF. The FUSE lookup and post-read getattr return the same inode and size but different mtime/blocks. The source supports a specific attribute-cache transition hypothesis: write refreshes cached size only, lookup can reuse the stale attributes, and open subsequently refreshes the userspace inode cache from the store. The same four source files are byte-identical in ca07159, corrected baseline 4f982, pre-cleanup candidate 7b844 and corrected candidate c8f5.

The initial fstat values and content SHA were not saved in the failed helper result. This is not a content-failure or cleanup-causality conclusion. Keep the failed run and strict predicate; the diagnostic helper must expose the exact changed fields and full oracle result. See review.json for source anchors, raw-log SHA and all read requests.
