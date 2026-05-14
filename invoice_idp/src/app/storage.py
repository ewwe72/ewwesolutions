"""S3-compatible object storage for invoice PDFs.

Targets MinIO in dev (docker-compose), Hetzner Object Storage in prod.
The interface is sync boto3 — fast enough for invoice-sized PDFs
(<=20 MB per §6); callers in async contexts wrap with
`asyncio.to_thread(...)` so the loop isn't blocked.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import boto3
from botocore.client import Config

from src.app.config import get_settings


class ObjectStorage:
    """Thin wrapper around the S3 PutObject / GetObject surface we need."""

    def __init__(
        self,
        endpoint_url: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        region: str,
    ) -> None:
        self._bucket = bucket
        self._client: Any = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
            config=Config(signature_version="s3v4"),
        )

    @property
    def bucket(self) -> str:
        return self._bucket

    def put(
        self,
        key: str,
        content: bytes,
        content_type: str = "application/pdf",
    ) -> None:
        self._client.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=content,
            ContentType=content_type,
        )

    def get(self, key: str) -> bytes:
        response = self._client.get_object(Bucket=self._bucket, Key=key)
        body: bytes = response["Body"].read()
        return body

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self._bucket, Key=key)

    def exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
            return True
        except self._client.exceptions.ClientError:
            return False

    def presigned_get_url(self, key: str, expires_in: int = 3600) -> str:
        url: str = self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=expires_in,
        )
        return url

    def ensure_bucket(self) -> None:
        """Create the bucket if it doesn't exist. No-op when it does."""
        try:
            self._client.head_bucket(Bucket=self._bucket)
        except self._client.exceptions.ClientError:
            self._client.create_bucket(Bucket=self._bucket)


@lru_cache(maxsize=1)
def get_storage() -> ObjectStorage:
    """Return the process-wide storage client, configured from settings."""
    s = get_settings()
    return ObjectStorage(
        endpoint_url=s.s3_endpoint_url,
        access_key=s.s3_access_key,
        secret_key=s.s3_secret_key,
        bucket=s.s3_bucket,
        region=s.s3_region,
    )
