#!/usr/bin/env python3
"""Create an owned temporary bucket, verify PUT/GET/range/LIST, then remove only it."""
import argparse
from contextlib import closing
import hashlib
import json
import os
import uuid

def run_probe(client):
    bucket = "ax-probe-" + uuid.uuid4().hex
    payload = bytes(range(256)) * 4096
    created = False
    try:
        client.create_bucket(Bucket=bucket)
        created = True
        client.put_object(Bucket=bucket, Key="probe.bin", Body=payload)
        head = client.head_object(Bucket=bucket, Key="probe.bin")
        if head.get("ContentLength") != len(payload):
            raise RuntimeError("S3 HEAD returned an incorrect content length")
        with closing(client.get_object(Bucket=bucket, Key="probe.bin")["Body"]) as body:
            if body.read() != payload:
                raise RuntimeError("S3 full GET content differs from uploaded bytes")
        with closing(client.get_object(Bucket=bucket, Key="probe.bin", Range="bytes=4096-8191")["Body"]) as body:
            if body.read() != payload[4096:8192]:
                raise RuntimeError("S3 range GET content differs from requested bytes")
        listing = client.list_objects_v2(Bucket=bucket)
        if listing.get("IsTruncated", False) or [obj["Key"] for obj in listing.get("Contents", [])] != ["probe.bin"]:
            raise RuntimeError("S3 LIST did not return exactly the uploaded object")
    finally:
        if created:
            # A failed cleanup prevents success from being returned or printed.
            client.delete_object(Bucket=bucket, Key="probe.bin")
            client.delete_bucket(Bucket=bucket)
    return {"probe": "passed", "cleanup": "passed", "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest(), "scope": "S3 API correctness only; no durability or EC qualification"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", required=True)
    args = parser.parse_args()
    import boto3
    from botocore.config import Config

    client = boto3.client(
        "s3", endpoint_url=args.endpoint,
        region_name=os.environ.get("AWS_REGION", "us-east-1"),
        config=Config(signature_version="s3v4", connect_timeout=5, read_timeout=30,
                      retries={"max_attempts": 0}, s3={"addressing_style": "path"}),
    )
    try:
        result = run_probe(client)
    finally:
        client.close()
    print(json.dumps(result))


if __name__ == "__main__":
    main()
