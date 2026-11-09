"""Policy document contents in Amazon S3: one private, encrypted, never-overwritten object per revision.

MongoDB keeps each revision's metadata and a reference to its object (provider, bucket, region, key, version_id);
the Markdown text itself lives only here. Every write is create-only (If-None-Match: *), so a revision's object is
never replaced, and every read is checked against the SHA-256 that MongoDB recorded.

Credentials come from boto3's standard provider chain (environment, shared config, instance role); this module never
reads, logs or returns them. Errors name the object key and the S3 error code only: never a credential or the text.
"""

import base64
import hashlib
import logging
from dataclasses import dataclass

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from expense_api.config import Settings
from expense_api.errors import ApiError

log = logging.getLogger(__name__)

CONTENT_TYPE = "text/markdown; charset=utf-8"
SHA256_METADATA = "content-sha256"  # x-amz-meta-content-sha256: the object's own record of its hash
MISSING = {"NoSuchKey", "404", "NotFound"}
DENIED = {"AccessDenied", "403", "InvalidAccessKeyId", "SignatureDoesNotMatch", "ExpiredToken", "AccountProblem"}


class ObjectExists(Exception):
    """A create-only write found an object already at the key."""


@dataclass(frozen=True)
class StoredObject:
    bucket: str
    region: str
    key: str
    version_id: str | None

    def reference(self) -> dict:
        return {"provider": "s3", "bucket": self.bucket, "region": self.region, "key": self.key, "version_id": self.version_id}


def revision_key(prefix: str, document_id: str, revision: int) -> str:
    """policy-documents/{document_id}/revisions/{revision}/document.md. The ID is the server's, so no user text is in it."""
    return f"{prefix.strip('/')}/{document_id}/revisions/{revision}/document.md"


def ruleset_document_key(prefix: str, ruleset_id: str, sha256: str) -> str:
    """ruleset-documents/{ruleset_id}/documents/{sha256}.md: one object per distinct text a version compiled from."""
    return f"{prefix.strip('/')}/{ruleset_id}/documents/{sha256}.md"


class DocumentStore:
    """S3 reads and create-only writes of Markdown revisions. Built per deployment; holds no request state."""

    def __init__(self, settings: Settings, client=None):
        if not settings.s3_bucket:
            raise ApiError(503, "document_storage_unavailable", "policy document storage is not configured: set AWS_S3_BUCKET")
        self.bucket, self.region, self.prefix, self.ruleset_prefix = settings.s3_bucket, settings.s3_region, settings.s3_document_prefix, settings.s3_ruleset_prefix
        self.kms_key_id = settings.s3_kms_key_id
        self.client = client or boto3.client(
            "s3",
            region_name=settings.s3_region,
            endpoint_url=settings.s3_endpoint_url,
            config=Config(connect_timeout=settings.s3_timeout_seconds, read_timeout=settings.s3_timeout_seconds, retries={"max_attempts": 3, "mode": "standard"}),
        )

    def key(self, document_id: str, revision: int) -> str:
        return revision_key(self.prefix, document_id, revision)

    def ruleset_key(self, ruleset_id: str, sha256: str) -> str:
        """A compiled version's document, by the version's ID (fixed before it is stored) and the text's hash."""
        return ruleset_document_key(self.ruleset_prefix, ruleset_id, sha256)

    def put(self, key: str, data: bytes, sha256: str) -> StoredObject:
        """Write a new object, only if none is at the key. S3 checks the body against its SHA-256 on arrival.

        Raises:
            ObjectExists: If an object is already at the key: it is never replaced.
            ApiError: 503 if S3 refuses or cannot be reached.
        """
        encryption = {"ServerSideEncryption": "aws:kms", "SSEKMSKeyId": self.kms_key_id} if self.kms_key_id else {"ServerSideEncryption": "AES256"}
        try:
            response = self.client.put_object(
                Bucket=self.bucket, Key=key, Body=data, IfNoneMatch="*", ContentType=CONTENT_TYPE,
                ChecksumSHA256=base64.b64encode(bytes.fromhex(sha256)).decode(), Metadata={SHA256_METADATA: sha256}, **encryption,
            )
        except ClientError as error:
            if _code(error) in ("PreconditionFailed", "412", "ConditionalRequestConflict"):
                raise ObjectExists(key) from None
            raise _unavailable("write", key, error) from None
        except BotoCoreError as error:
            raise _unavailable("write", key, error) from None
        return StoredObject(self.bucket, self.region, key, response.get("VersionId"))

    def read(self, storage: dict, sha256: str, max_bytes: int) -> str:
        """The text of a stored revision, from its own reference, checked against the hash MongoDB recorded.

        Raises:
            ApiError: 500 document_content_missing, 500 integrity_error, or 503 document_storage_unavailable.
        """
        if storage.get("provider") != "s3":
            raise ApiError(500, "integrity_error", f"unknown document storage {storage.get('provider')!r}")
        extra = {"VersionId": storage["version_id"]} if storage.get("version_id") else {}
        key = storage["key"]
        try:
            response = self.client.get_object(Bucket=storage["bucket"], Key=key, **extra)
            data = response["Body"].read(max_bytes + 1)
        except ClientError as error:
            if _code(error) in MISSING or _code(error) == "NoSuchVersion":
                log.error("document object missing: %s", key)
                raise ApiError(500, "document_content_missing", f"the stored content of {key} is missing") from None
            raise _unavailable("read", key, error) from None
        except BotoCoreError as error:
            raise _unavailable("read", key, error) from None
        if len(data) > max_bytes or hashlib.sha256(data).hexdigest() != sha256:
            log.error("document object does not match its recorded hash: %s", key)
            raise ApiError(500, "integrity_error", f"the stored content of {key} does not match its recorded hash")
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            raise ApiError(500, "integrity_error", f"the stored content of {key} is not UTF-8") from None

    def existing(self, key: str) -> StoredObject | None:
        """The object at a key, with its version, or None when there is none."""
        try:
            response = self.client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as error:
            if _code(error) in MISSING:
                return None
            raise _unavailable("read", key, error) from None
        except BotoCoreError as error:
            raise _unavailable("read", key, error) from None
        return StoredObject(self.bucket, self.region, key, response.get("VersionId"))

    def delete(self, stored: StoredObject) -> None:
        """Remove an object this process wrote and nothing references: the exact version, where S3 keeps versions."""
        extra = {"VersionId": stored.version_id} if stored.version_id else {}
        self.client.delete_object(Bucket=stored.bucket, Key=stored.key, **extra)

    def keys(self, document_prefix: str = "", root: str | None = None) -> list[str]:
        """Every object key under the document prefix (or another root, such as the ruleset prefix), for reconciliation."""
        prefix = f"{(root or self.prefix).strip('/')}/{document_prefix}"
        found = []
        for page in self.client.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=prefix):
            found.extend(item["Key"] for item in page.get("Contents", []))
        return found


def _code(error: ClientError) -> str:
    return str(error.response.get("Error", {}).get("Code", ""))


def _unavailable(action: str, key: str, error: Exception) -> ApiError:
    """A clear 503 that names the key and S3's error code, never credentials or content."""
    code = _code(error) if isinstance(error, ClientError) else type(error).__name__
    log.error("document storage %s failed for %s: %s", action, key, code)
    reason = "S3 denied access" if code in DENIED else "S3 could not be reached in time" if "Timeout" in code or "Connect" in code or "Endpoint" in code else f"S3 refused ({code})"
    return ApiError(503, "document_storage_unavailable", f"policy document storage could not {action} {key}: {reason}")
