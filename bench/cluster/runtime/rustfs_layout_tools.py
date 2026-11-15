#!/usr/bin/env python3
"""Inspect persisted RustFS EC 2+2 layouts and evict bucket page caches.

Strictly zero hashes/checksums: decodes `xl.meta` (`XL2 ` v1.3 MsgPack) via pure Python `struct`
to read persisted `EcM`, `EcN`, `EcBSize`, `Size`, and `x-rustfs-internal-ec-read-quantum`.
"""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import struct
import time

import cluster_lib as cl

RUSTFS_HOSTS = ("A672", "A601", "A635", "A368")

# Self-contained remote Python snippet executed on each RustFS host to decode xl.meta without hashes
_REMOTE_INSPECT_SNIPPET = r'''
import json, os, struct, sys
from pathlib import Path

def mp_decode(buf, idx=0):
    b = buf[idx]
    idx += 1
    if b <= 0x7f:
        return b, idx
    if b >= 0xe0:
        return b - 0x100, idx
    if 0x80 <= b <= 0x8f:
        n = b & 0x0f
        out = {}
        for _ in range(n):
            k, idx = mp_decode(buf, idx)
            v, idx = mp_decode(buf, idx)
            out[k] = v
        return out, idx
    if 0x90 <= b <= 0x9f:
        n = b & 0x0f
        out = []
        for _ in range(n):
            v, idx = mp_decode(buf, idx)
            out.append(v)
        return out, idx
    if 0xa0 <= b <= 0xbf:
        n = b & 0x1f
        return buf[idx:idx+n].decode("utf-8", errors="replace"), idx + n
    if b == 0xc0:
        return None, idx
    if b == 0xc2:
        return False, idx
    if b == 0xc3:
        return True, idx
    if b in (0xc4, 0xc5, 0xc6):
        fmt, sz = {0xc4: (">B", 1), 0xc5: (">H", 2), 0xc6: (">I", 4)}[b]
        n = struct.unpack_from(fmt, buf, idx)[0]
        idx += sz
        return bytes(buf[idx:idx+n]), idx + n
    if b in (0xc7, 0xc8, 0xc9):
        fmt, sz = {0xc7: (">Bb", 2), 0xc8: (">Hb", 3), 0xc9: (">Ib", 5)}[b]
        n, _t = struct.unpack_from(fmt, buf, idx)
        idx += sz
        return bytes(buf[idx:idx+n]), idx + n
    if b == 0xca:
        return struct.unpack_from(">f", buf, idx)[0], idx + 4
    if b == 0xcb:
        return struct.unpack_from(">d", buf, idx)[0], idx + 8
    if b in (0xcc, 0xcd, 0xce, 0xcf):
        fmt, sz = {0xcc: (">B", 1), 0xcd: (">H", 2), 0xce: (">I", 4), 0xcf: (">Q", 8)}[b]
        return struct.unpack_from(fmt, buf, idx)[0], idx + sz
    if b in (0xd0, 0xd1, 0xd2, 0xd3):
        fmt, sz = {0xd0: (">b", 1), 0xd1: (">h", 2), 0xd2: (">i", 4), 0xd3: (">q", 8)}[b]
        return struct.unpack_from(fmt, buf, idx)[0], idx + sz
    if 0xd4 <= b <= 0xd8:
        sz = 1 << (b - 0xd4)
        return bytes(buf[idx + 1 : idx + 1 + sz]), idx + 1 + sz
    if b in (0xd9, 0xda, 0xdb):
        fmt, sz = {0xd9: (">B", 1), 0xda: (">H", 2), 0xdb: (">I", 4)}[b]
        n = struct.unpack_from(fmt, buf, idx)[0]
        idx += sz
        return buf[idx:idx+n].decode("utf-8", errors="replace"), idx + n
    if b in (0xdc, 0xdd):
        fmt, sz = {0xdc: (">H", 2), 0xdd: (">I", 4)}[b]
        n = struct.unpack_from(fmt, buf, idx)[0]
        idx += sz
        out = []
        for _ in range(n):
            v, idx = mp_decode(buf, idx)
            out.append(v)
        return out, idx
    if b in (0xde, 0xdf):
        fmt, sz = {0xde: (">H", 2), 0xdf: (">I", 4)}[b]
        n = struct.unpack_from(fmt, buf, idx)[0]
        idx += sz
        out = {}
        for _ in range(n):
            k, idx = mp_decode(buf, idx)
            v, idx = mp_decode(buf, idx)
            out[k] = v
        return out, idx
    raise ValueError(f"Unsupported msgpack tag 0x{b:02x} at {idx-1}")

def parse_xl_meta(raw: bytes) -> dict:
    if raw[:4] != b"XL2 ":
        raise ValueError(f"Unexpected xl.meta header: {raw[:4]!r}")
    maj, minv = struct.unpack_from("<HH", raw, 4)
    outer, next_idx = mp_decode(raw, 8)
    versions = []
    if isinstance(outer, (bytes, bytearray)):
        first_item, pos = mp_decode(outer, 0)
        if isinstance(first_item, dict):
            versions = first_item.get("Versions") or first_item.get("versions") or []
        elif isinstance(first_item, int):
            # XL2 v1.3 sequential stream: hdr_ver, meta_ver, n_versions, then pairs of (hdr_bin, ver_bin)
            _meta_ver, pos = mp_decode(outer, pos)
            n_ver, pos = mp_decode(outer, pos)
            for _ in range(int(n_ver)):
                if pos >= len(outer):
                    break
                _vhdr, pos = mp_decode(outer, pos)
                if pos >= len(outer):
                    break
                vbody, pos = mp_decode(outer, pos)
                if isinstance(vbody, (bytes, bytearray)):
                    vdoc, _ = mp_decode(vbody, 0)
                    versions.append(vdoc)
                elif isinstance(vbody, dict):
                    versions.append(vbody)
    elif isinstance(outer, dict):
        versions = outer.get("Versions") or outer.get("versions") or []
    # Find latest V2Obj version entry
    v2 = None
    for v in reversed(versions):
        if isinstance(v, dict):
            v2 = v.get("V2Obj") or v.get("ObjV2") or v.get("v2_obj") or v
            if isinstance(v2, dict) and ("EcBSize" in v2 or "Size" in v2):
                break
    if not isinstance(v2, dict):
        return {"header_version": [maj, minv], "versions_decoded": len(versions)}
    meta_sys = {}
    for k, val in (v2.get("MetaSys") or {}).items():
        if isinstance(val, (bytes, bytearray)):
            meta_sys[str(k)] = val.decode("utf-8", errors="replace")
        else:
            meta_sys[str(k)] = str(val)
    meta_usr = {}
    for k, val in (v2.get("MetaUsr") or {}).items():
        if isinstance(val, (bytes, bytearray)):
            meta_usr[str(k)] = val.decode("utf-8", errors="replace")
        else:
            meta_usr[str(k)] = str(val)
    q_raw = meta_sys.get("x-rustfs-internal-ec-read-quantum") or meta_usr.get("x-rustfs-internal-ec-read-quantum")
    ec_m = int(v2.get("EcM", 2))
    ec_n = int(v2.get("EcN", 2))
    ec_b = int(v2.get("EcBSize", 0))
    eff_q = int(q_raw) if (q_raw and str(q_raw).isdigit()) else (ec_b // ec_m if ec_m > 0 else 0)
    return {
        "header_version": [maj, minv],
        "ec_m": ec_m,
        "ec_n": ec_n,
        "ec_index": v2.get("EcIndex"),
        "actual_B": ec_b,
        "raw_quantum_meta": q_raw,
        "actual_q": eff_q,
        "logical_size": int(v2.get("Size", 0)),
        "hint_meta": meta_usr.get("rustfs-ec-block-size-hint") or meta_usr.get("x-amz-meta-rustfs-ec-block-size-hint"),
    }

req = json.loads(sys.stdin.read())
data_dir = Path(req["data_dir"])
bucket = req["bucket"]
max_samples = int(req.get("max_samples", 8))
keys = req.get("keys")

bdir = data_dir / bucket
samples = []
if bdir.is_dir():
    if keys:
        xl_paths = [bdir / k / "xl.meta" for k in keys if (bdir / k / "xl.meta").is_file()]
    else:
        xl_paths = []
        for root, dirs, files in os.walk(bdir):
            if "xl.meta" in files:
                xl_paths.append(Path(root) / "xl.meta")
                if len(xl_paths) >= max_samples:
                    break
    for xlp in xl_paths[:max_samples]:
        rel_key = xlp.parent.relative_to(bdir).as_posix()
        meta_info = parse_xl_meta(xlp.read_bytes())
        part_sizes = []
        for sub in sorted(xlp.parent.iterdir()):
            if sub.is_dir():
                for pf in sorted(sub.glob("part.*")):
                    if pf.is_file():
                        part_sizes.append(pf.stat().st_size)
        meta_info["key"] = rel_key
        meta_info["xl_meta_bytes"] = xlp.stat().st_size
        meta_info["shard_part_sizes"] = part_sizes
        samples.append(meta_info)

print(json.dumps({"bucket_exists": bdir.is_dir(), "samples": samples}))
'''

_REMOTE_EVICT_SNIPPET = r'''
import json, os, sys
from pathlib import Path

req = json.loads(sys.stdin.read())
data_dir = Path(req["data_dir"])
buckets = req["buckets"]
POSIX_FADV_DONTNEED = getattr(os, "POSIX_FADV_DONTNEED", 4)
files_evicted = 0
bytes_evicted = 0
for b in buckets:
    if not b.startswith(req["prefix"] + "-"):
        raise ValueError(f"Refusing fadvise outside axd1- bucket: {b}")
    bdir = data_dir / b
    if not bdir.is_dir():
        continue
    for root, _dirs, files in os.walk(bdir):
        for fn in files:
            fp = os.path.join(root, fn)
            try:
                fd = os.open(fp, os.O_RDONLY)
                try:
                    st = os.fstat(fd)
                    if st.st_size > 0:
                        os.posix_fadvise(fd, 0, 0, POSIX_FADV_DONTNEED)
                        bytes_evicted += st.st_size
                    files_evicted += 1
                finally:
                    os.close(fd)
            except OSError:
                pass
print(json.dumps({"files_evicted": files_evicted, "bytes_evicted": bytes_evicted}))
'''


def discover_rustfs_nodes() -> dict[str, dict]:
    """Discovers container name, /data/rustfs0 host source path, and compose file on A672..A368."""
    if cl.RUSTFS_BACKEND == 'official':
        config = cl.RUSTFS_BACKENDS['official']
        return {host: {'unit': config['unit'], 'data_dir': config['data_dir'],
                       'version': config['version'], 'endpoint': endpoint,
                       'quantum_enable': 'unsupported', 'block_hint_enable': 'unsupported'}
                for host, endpoint in zip(config['nodes'], config['endpoints'])}
    def _one(hname: str) -> tuple[str, dict]:
        py = (
            "import json, subprocess\n"
            "names = subprocess.check_output(['docker', 'ps', '--format', '{{.Names}}'], text=True).splitlines()\n"
            "cand = [n for n in names if n == 'ax-cluster-direct-run001-rustfs-1'][0]\n"
            "info = json.loads(subprocess.check_output(['docker', 'inspect', cand], text=True))[0]\n"
            "mounts = {m['Destination']: m['Source'] for m in info['Mounts']}\n"
            "labels = info['Config'].get('Labels') or {}\n"
            "env = dict(e.split('=', 1) for e in (info['Config'].get('Env') or []) if '=' in e)\n"
            "print(json.dumps({\n"
            "  'container': cand,\n"
            "  'image': info['Config']['Image'],\n"
            "  'memory_bytes': info['HostConfig']['Memory'],\n"
            "  'nano_cpus': info['HostConfig']['NanoCpus'],\n"
            "  'data_dir': mounts.get('/data/rustfs0', ''),\n"
            "  'compose_dir': labels.get('com.docker.compose.project.working_dir', ''),\n"
            "  'compose_files': labels.get('com.docker.compose.project.config_files', ''),\n"
            "  'quantum_enable': env.get('RUSTFS_EC_READ_QUANTUM_ENABLE', ''),\n"
            "  'block_hint_enable': env.get('RUSTFS_EC_BLOCK_SIZE_HINT_ENABLE', ''),\n"
            "}))\n"
        )
        p = cl.run_on_host(hname, ["python3", "-c", py], log_cmd=False)
        return hname, json.loads(p.stdout.decode("utf-8"))

    with ThreadPoolExecutor(max_workers=4) as pool:
        return dict(pool.map(_one, RUSTFS_HOSTS))


def inspect_bucket_layout(nodes_meta: dict[str, dict], bucket: str, keys: list[str] | None = None, max_samples: int = 8) -> dict:
    def _one(hname: str) -> tuple[str, dict]:
        payload = json.dumps(
            {
                "data_dir": nodes_meta[hname]["data_dir"],
                "bucket": bucket,
                "keys": keys,
                "max_samples": max_samples,
            }
        ).encode("utf-8")
        p = cl.run_on_host(hname, ["python3", "-c", _REMOTE_INSPECT_SNIPPET], input_bytes=payload, log_cmd=False)
        return hname, json.loads(p.stdout.decode("utf-8"))

    with ThreadPoolExecutor(max_workers=4) as pool:
        per_host = dict(pool.map(_one, RUSTFS_HOSTS))

    # Summarize largest data sample across hosts so small marker/manifest objects never shadow data chunks
    summary = {}
    for h in RUSTFS_HOSTS:
        samps = [s for s in per_host[h].get("samples", []) if "actual_B" in s]
        if samps:
            s0 = max(samps, key=lambda s: s.get("logical_size", 0))
            k0 = s0["key"]
            summary = {
                "key": k0,
                "ec_m": s0["ec_m"],
                "ec_n": s0["ec_n"],
                "actual_B": s0["actual_B"],
                "actual_q": s0["actual_q"],
                "raw_quantum_meta": s0["raw_quantum_meta"],
                "logical_size": s0["logical_size"],
                "hint_meta": s0.get("hint_meta"),
                "shard_sizes_by_host": {
                    hh: next(
                        (ss["shard_part_sizes"] for ss in per_host[hh].get("samples", []) if ss.get("key") == k0),
                        (per_host[hh]["samples"][0]["shard_part_sizes"] if per_host[hh].get("samples") else []),
                    )
                    for hh in RUSTFS_HOSTS
                },
            }
            break
    return {"bucket": bucket, "summary": summary, "hosts": per_host}


def evict_bucket_page_cache(nodes_meta: dict[str, dict], buckets: list[str]) -> dict[str, dict]:
    """Drops kernel page cache ONLY for files inside our `axd1-*` buckets on the 4 RustFS hosts."""
    def _one(hname: str) -> tuple[str, dict]:
        payload = json.dumps(
            {
                "data_dir": nodes_meta[hname]["data_dir"],
                "buckets": buckets,
                "prefix": cl.RUN_PREFIX,
            }
        ).encode("utf-8")
        p = cl.run_on_host(hname, ["python3", "-c", _REMOTE_EVICT_SNIPPET], input_bytes=payload, log_cmd=False)
        return hname, json.loads(p.stdout.decode("utf-8"))

    with ThreadPoolExecutor(max_workers=4) as pool:
        return dict(pool.map(_one, RUSTFS_HOSTS))
