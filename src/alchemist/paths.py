"""Filesystem helpers: every path is a cloudpathlib AnyPath, so the same code
works on s3:// URIs and on local folders (tests, laptops)."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

# Several processes (the orchestrator and the sandbox kernel) read and write the same run folder, so a local
# copy of an S3 object must never be trusted across calls: drop it as soon as the file is closed.
os.environ.setdefault("CLOUDPATHLIB_FILE_CACHE_MODE", "close_file")

from cloudpathlib import AnyPath, CloudPath  # noqa: E402

_ULID_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_id() -> str:
    """A ULID: 48-bit millisecond time + 80 random bits, Crockford base32."""
    value = (int(time.time() * 1000) << 80) | secrets.randbits(80)
    chars = []
    for _ in range(26):
        chars.append(_ULID_ALPHABET[value & 31])
        value >>= 5
    return "".join(reversed(chars))


def as_path(uri: str | os.PathLike) -> Path | CloudPath:
    return AnyPath(str(uri))


def is_cloud(path) -> bool:
    return isinstance(path, CloudPath)


def write_text(path, text: str) -> None:
    path = as_path(path)
    if not is_cloud(path):
        path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def create_exclusive(path, text: str) -> None:
    """Write a file only if it does not exist yet (S3 conditional put; O_EXCL locally)."""
    path = as_path(path)
    if is_cloud(path):
        from botocore.exceptions import ClientError

        try:
            path.client.client.put_object(Bucket=path.bucket, Key=path.key, Body=text.encode(), IfNoneMatch="*")
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("PreconditionFailed", "ConditionalRequestConflict"):
                raise FileExistsError(str(path)) from e
            raise
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "x") as f:
        f.write(text)


def s3_endpoint() -> str | None:
    """Custom S3 endpoint (MinIO, moto, LocalStack), read from the standard AWS variables."""
    return os.environ.get("AWS_ENDPOINT_URL_S3") or os.environ.get("AWS_ENDPOINT_URL") or None


def duckdb_s3_secret() -> str:
    """DuckDB S3 secret built from the environment; keys never appear in a notebook.

    With per-run credentials in the environment the secret uses exactly those keys, so
    DuckDB cannot fall back to any other identity; otherwise it uses the AWS credential chain.
    """
    def lit(v: str) -> str:
        return "'" + v.replace("'", "''") + "'"

    opts = ["type s3"]
    if os.environ.get("AWS_SESSION_TOKEN") and os.environ.get("AWS_ACCESS_KEY_ID"):
        opts += ["provider config", f"key_id {lit(os.environ['AWS_ACCESS_KEY_ID'])}",
                 f"secret {lit(os.environ['AWS_SECRET_ACCESS_KEY'])}", f"session_token {lit(os.environ['AWS_SESSION_TOKEN'])}"]
    else:
        opts.append("provider credential_chain")
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    if region:
        opts.append(f"region {lit(region)}")
    endpoint = s3_endpoint()
    if endpoint:
        u = urlparse(endpoint)
        opts += [f"endpoint {lit(u.netloc or u.path)}", "url_style 'path'", f"use_ssl {str(u.scheme == 'https').lower()}"]
    return f"create secret ({', '.join(opts)})"


def write_json(path, data: Any) -> None:
    write_text(path, json.dumps(data, indent=2, sort_keys=False, default=str) + "\n")


def read_json(path) -> Any:
    return json.loads(as_path(path).read_text())


def write_yaml(path, data: Any) -> None:
    write_text(path, yaml.safe_dump(data, sort_keys=False, allow_unicode=True))


def read_yaml(path) -> Any:
    return yaml.safe_load(as_path(path).read_text())


def parse_yaml_text(text: str) -> Any:
    """Parse YAML or JSON text; YAML is a superset of JSON, so one parser fits both."""
    return yaml.safe_load(text)


def read_structured(path) -> Any:
    """Read a YAML or JSON file."""
    return parse_yaml_text(as_path(path).read_text())


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with as_path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def md5_file(path) -> str:
    h = hashlib.md5()
    with as_path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def object_info(path) -> dict:
    """Key, ETag and last-modified time of a source object, recorded for drift checks."""
    path = as_path(path)
    if is_cloud(path):
        stat = path.stat()
        etag = getattr(path, "etag", None)
        return {"key": str(path), "etag": (etag or "").strip('"'), "size": stat.st_size,
                "last_modified": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(stat.st_mtime))}
    stat = path.stat()
    # Local stand-in for an S3 ETag: the MD5 of the content (what S3 uses for single-part uploads).
    return {"key": str(path), "etag": md5_file(path), "size": stat.st_size,
            "last_modified": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(stat.st_mtime))}


def put_file(local: Path, dest) -> None:
    dest = as_path(dest)
    if is_cloud(dest):
        dest.upload_from(local, force_overwrite_to_cloud=True)
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(local, dest)


def copy_tree(src, dest) -> None:
    src, dest = as_path(src), as_path(dest)
    for f in src.rglob("*"):
        if f.is_file():
            target = dest / str(f.relative_to(src))
            if is_cloud(target) or is_cloud(f):
                target.write_bytes(f.read_bytes())
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(f, target)


def expand_glob(pattern: str) -> list:
    """Expand a dataset glob once, so a query runs against an explicit, recorded file list."""
    if "://" in pattern:
        root, _, glob = _split_glob(pattern)
        return sorted(as_path(root).glob(glob), key=str)
    root, _, glob = _split_glob(pattern)
    return sorted(Path(root).glob(glob), key=str)


def _split_glob(pattern: str) -> tuple[str, str, str]:
    parts = pattern.split("/")
    for i, part in enumerate(parts):
        if any(c in part for c in "*?["):
            return "/".join(parts[:i]), "/", "/".join(parts[i:])
    return "/".join(parts[:-1]), "/", parts[-1]
