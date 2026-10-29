"""HB0: where the R2 history store is read from - an injectable, read-only object store.

`R2Store` talks to Cloudflare R2 over the S3 API (boto3), with credentials from the environment only:
R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET, R2_ENDPOINT. Nothing is ever written to the bucket.
`LocalDirStore` reads the same layout from a directory (tests, or an operator who synced the bucket to disk first).
Both are synchronous; the importer calls them in a worker thread.
"""
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, List, Optional, Protocol

R2_ENV = ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET", "R2_ENDPOINT")


@dataclass(frozen=True)
class StoredObject:
    key: str
    data: bytes
    etag: Optional[str] = None


class ObjectStore(Protocol):
    def list_keys(self, prefix: str) -> Iterator[str]: ...

    def get(self, key: str) -> StoredObject: ...


class LocalDirStore:
    """The bucket layout under a local directory. Keys are '/'-separated paths relative to `root`."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root != path and self.root not in path.parents:
            raise ValueError(f"Key {key!r} points outside the store")
        return path

    def list_keys(self, prefix: str) -> Iterator[str]:
        for path in sorted(self.root.rglob("*")):
            if path.is_file():
                key = path.relative_to(self.root).as_posix()
                if key.startswith(prefix):
                    yield key

    def get(self, key: str) -> StoredObject:
        path = self._path(key)
        if not path.is_file():
            raise FileNotFoundError(f"No object {key!r}")
        return StoredObject(key, path.read_bytes())


class R2Store:
    """Cloudflare R2 through the S3 API. Read-only: list and get."""

    def __init__(self, *, bucket: str, endpoint: str, access_key_id: str, secret_access_key: str, client=None) -> None:
        self.bucket = bucket
        if client is None:
            import boto3  # imported here so the API process never needs it

            client = boto3.client("s3", endpoint_url=endpoint, aws_access_key_id=access_key_id,
                                  aws_secret_access_key=secret_access_key, region_name="auto")
        self._client = client

    @classmethod
    def from_env(cls) -> "R2Store":
        missing: List[str] = [name for name in R2_ENV if not os.environ.get(name, "").strip()]
        if missing:
            raise RuntimeError(f"R2 is not configured: set {', '.join(missing)} in the server environment")
        return cls(bucket=os.environ["R2_BUCKET"].strip(), endpoint=os.environ["R2_ENDPOINT"].strip(),
                   access_key_id=os.environ["R2_ACCESS_KEY_ID"].strip(), secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"].strip())

    def list_keys(self, prefix: str) -> Iterator[str]:
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for item in page.get("Contents") or []:
                yield item["Key"]

    def get(self, key: str) -> StoredObject:
        response = self._client.get_object(Bucket=self.bucket, Key=key)
        return StoredObject(key, response["Body"].read(), (response.get("ETag") or "").strip('"') or None)
