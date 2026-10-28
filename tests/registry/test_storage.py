import hashlib

import boto3
import pytest
import zstandard
from moto import mock_aws

from kb_registry import storage
from kb_registry.settings import Settings
from kb_registry.storage import FileBlobStore, S3BlobStore, make_store, verify_and_size

DATA = b"hello pack " * 100
H = hashlib.sha256(DATA).hexdigest()
Z = zstandard.ZstdCompressor().compress(DATA)


def test_file_store_roundtrip(tmp_path):
    s = FileBlobStore(tmp_path)
    assert not s.exists(H)
    s.put(H, Z)
    assert s.exists(H) and s.get(H) == Z
    assert (tmp_path / H[:2] / H).exists()


def test_s3_store_roundtrip():
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="kb-packs")
        s = S3BlobStore("kb-packs", None)
        assert not s.exists(H)
        s.put(H, Z)
        assert s.exists(H) and s.get(H) == Z


def test_verify_returns_uncompressed_size():
    assert verify_and_size(H, Z) == len(DATA)


def test_verify_rejects_mismatch():
    with pytest.raises(ValueError, match="hash mismatch"):
        verify_and_size("0" * 64, Z)


def test_verify_rejects_garbage():
    with pytest.raises(ValueError):
        verify_and_size(H, b"not zstd")


def test_verify_rejects_oversize():
    with pytest.raises(ValueError, match="too large"):
        verify_and_size(H, Z, max_bytes=10)


def test_make_store_parses_urls(tmp_path):
    assert isinstance(make_store(Settings(storage=f"file://{tmp_path}")), FileBlobStore)
    with mock_aws():
        st = make_store(Settings(storage="s3://kb-packs", s3_endpoint=None))
        assert isinstance(st, S3BlobStore) and st.bucket == "kb-packs"
    with pytest.raises(ValueError):
        make_store(Settings(storage="ftp://x"))
