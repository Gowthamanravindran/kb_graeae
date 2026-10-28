"""Content-addressed blob storage. Blobs are stored exactly as uploaded: zstd-compressed bytes,
keyed by the sha256 of the uncompressed content."""

from __future__ import annotations

import hashlib
import io
import os
import tempfile
from pathlib import Path
from typing import Protocol

import zstandard

MAX_BLOB = 200 * 2**20


class BlobStore(Protocol):
    def put(self, hash: str, compressed: bytes) -> None: ...

    def get(self, hash: str) -> bytes: ...

    def exists(self, hash: str) -> bool: ...


class FileBlobStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _path(self, hash: str) -> Path:
        return self.root / hash[:2] / hash

    def put(self, hash: str, compressed: bytes) -> None:
        p = self._path(hash)
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=p.parent)
        with os.fdopen(fd, "wb") as f:
            f.write(compressed)
        os.replace(tmp, p)

    def get(self, hash: str) -> bytes:
        return self._path(hash).read_bytes()

    def exists(self, hash: str) -> bool:
        return self._path(hash).is_file()


class S3BlobStore:
    def __init__(self, bucket: str, endpoint: str | None) -> None:
        import boto3

        self.bucket = bucket
        self.s3 = boto3.client("s3", endpoint_url=endpoint)

    def _key(self, hash: str) -> str:
        return f"blobs/{hash}"

    def put(self, hash: str, compressed: bytes) -> None:
        self.s3.put_object(Bucket=self.bucket, Key=self._key(hash), Body=compressed)

    def get(self, hash: str) -> bytes:
        return self.s3.get_object(Bucket=self.bucket, Key=self._key(hash))["Body"].read()

    def exists(self, hash: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self.s3.head_object(Bucket=self.bucket, Key=self._key(hash))
            return True
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return False
            raise


def make_store(settings) -> BlobStore:
    url = settings.storage
    if url.startswith("file://"):
        return FileBlobStore(Path(url[len("file://"):]))
    if url.startswith("s3://"):
        return S3BlobStore(url[len("s3://"):].strip("/"), settings.s3_endpoint)
    raise ValueError(f"unsupported KB_REGISTRY_STORAGE: {url} (use file:///path or s3://bucket)")


def verify_and_size(hash: str, compressed: bytes, max_bytes: int = MAX_BLOB) -> int:
    """Decompress (streaming, bounded) and check sha256; returns the uncompressed size."""
    h = hashlib.sha256()
    size = 0
    try:
        with zstandard.ZstdDecompressor().stream_reader(io.BytesIO(compressed)) as r:
            while chunk := r.read(1 << 20):
                size += len(chunk)
                if size > max_bytes:
                    raise ValueError("too large")
                h.update(chunk)
    except zstandard.ZstdError as e:
        raise ValueError(f"not valid zstd data: {e}") from e
    if h.hexdigest() != hash:
        raise ValueError("hash mismatch")
    return size
