"""S3-compatible object storage (Section 4.3, 9.4).

Uploads originals with tenant-scoped keys, generates presigned GET URLs
for downloads, and supports lifecycle deletion when a document is removed.
"""

from typing import BinaryIO

import boto3
from botocore.client import Config

from app.config import settings


class ObjectStorage:
    def __init__(
        self,
        endpoint_url: str | None = None,
        bucket: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        region: str | None = None,
    ):
        self.bucket = bucket or settings.s3_bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url if endpoint_url is not None else settings.s3_endpoint_url,
            aws_access_key_id=access_key or settings.s3_access_key,
            aws_secret_access_key=secret_key or settings.s3_secret_key,
            region_name=region or settings.s3_region,
            config=Config(signature_version="s3v4"),
        )

    @staticmethod
    def key_for(tenant_id: str, document_id: str, filename: str) -> str:
        """Tenant-scoped object key — isolation enforced at the key level."""
        return f"tenants/{tenant_id}/documents/{document_id}/{filename}"

    def upload(self, key: str, data: bytes | BinaryIO, content_type: str) -> None:
        extra = {"ContentType": content_type}
        if isinstance(data, (bytes, bytearray)):
            self._client.put_object(Bucket=self.bucket, Key=key, Body=data, **extra)
        else:
            self._client.upload_fileobj(
                data, self.bucket, key, ExtraArgs=extra
            )

    def download(self, key: str) -> bytes:
        response = self._client.get_object(Bucket=self.bucket, Key=key)
        return response["Body"].read()

    def presigned_get_url(self, key: str, expires_in: int = 3600) -> str:
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=expires_in,
        )

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self.bucket, Key=key)

    def delete_prefix(self, prefix: str) -> int:
        """Delete all objects under a prefix (document cleanup job)."""
        deleted = 0
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            objects = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
            if objects:
                self._client.delete_objects(
                    Bucket=self.bucket, Delete={"Objects": objects}
                )
                deleted += len(objects)
        return deleted
