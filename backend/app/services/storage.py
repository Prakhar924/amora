"""S3-compatible object storage for profile photos.

The bucket is private. Clients never receive a durable URL:

* Upload — the API returns a presigned `PUT` so the file goes straight from the
  device to storage. Photo bytes never pass through the API process.
* Download — the API returns a short-lived presigned `GET` per request.

Presigning is a local HMAC computation, so these calls do no network I/O and
are safe to make from async request handlers.
"""

from __future__ import annotations

import logging
import uuid
from functools import lru_cache

import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import settings

logger = logging.getLogger(__name__)

_EXTENSIONS = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}


@lru_cache
def _client(public: bool = False):
    """Build a boto3 S3 client.

    `public=True` signs against the address a phone or browser can reach, which
    differs from the in-cluster endpoint under docker-compose.
    """
    endpoint = settings.s3_public_endpoint_url if public else settings.s3_endpoint_url
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        # MinIO requires path-style addressing; SigV4 is required by both.
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def build_photo_key(user_id: uuid.UUID, content_type: str) -> str:
    extension = _EXTENSIONS.get(content_type, "bin")
    return f"photos/{user_id}/{uuid.uuid4().hex}.{extension}"


def presign_upload(key: str, content_type: str, byte_size: int) -> dict:
    """Presigned PUT for a direct client upload.

    `ContentType` and `ContentLength` are part of the signature, so a client
    cannot substitute a different type or a much larger file than it declared.
    """
    url = _client(public=True).generate_presigned_url(
        "put_object",
        Params={
            "Bucket": settings.s3_bucket,
            "Key": key,
            "ContentType": content_type,
            "ContentLength": byte_size,
        },
        ExpiresIn=settings.s3_presign_ttl_seconds,
        HttpMethod="PUT",
    )
    return {
        "url": url,
        "headers": {
            "Content-Type": content_type,
            "Content-Length": str(byte_size),
        },
        "expires_in": settings.s3_presign_ttl_seconds,
    }


def presign_download(key: str) -> str:
    """Short-lived presigned GET. Called per response, never cached long-term."""
    return _client(public=True).generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.s3_bucket, "Key": key},
        ExpiresIn=settings.s3_presign_ttl_seconds,
        HttpMethod="GET",
    )


def object_exists(key: str) -> tuple[bool, int]:
    """Return `(exists, byte_size)`.

    Used to confirm an upload actually landed before a photo becomes eligible
    for moderation, so a client cannot register a photo it never sent.
    """
    try:
        head = _client().head_object(Bucket=settings.s3_bucket, Key=key)
        return True, int(head.get("ContentLength", 0))
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code in {"404", "NoSuchKey", "NotFound"}:
            return False, 0
        logger.warning("head_object failed for %s: %s", key, exc)
        return False, 0
    except BotoCoreError as exc:  # pragma: no cover - network/config failure
        logger.warning("Storage unreachable while heading %s: %s", key, exc)
        return False, 0


def delete_object(key: str) -> None:
    try:
        _client().delete_object(Bucket=settings.s3_bucket, Key=key)
    except (ClientError, BotoCoreError) as exc:
        # A failed delete must not fail the user's request; the object is
        # already unreferenced and can be swept up later.
        logger.warning("Failed to delete %s: %s", key, exc)


def ensure_bucket() -> bool:
    """Create the bucket if missing. Called once at startup in development."""
    client = _client()
    try:
        client.head_bucket(Bucket=settings.s3_bucket)
        return True
    except (ClientError, BotoCoreError):
        pass
    try:
        client.create_bucket(Bucket=settings.s3_bucket)
        logger.info("Created object storage bucket %s", settings.s3_bucket)
        return True
    except (ClientError, BotoCoreError) as exc:
        logger.warning("Could not create bucket %s: %s", settings.s3_bucket, exc)
        return False
