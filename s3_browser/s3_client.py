"""Pure read-only Amazon S3 client wrapper.

Guarantees zero write/modify/delete capabilities.
Includes a built-in Mock S3 client for offline Windows development & testing.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Generator, List, Optional, Tuple

import boto3
from botocore.exceptions import ClientError


@dataclass
class S3BucketItem:
    name: str
    creation_date: Optional[datetime] = None


@dataclass
class S3FolderItem:
    name: str
    prefix: str  # Full S3 prefix ending with '/'


@dataclass
class S3FileItem:
    name: str
    key: str  # Full S3 key
    size: int  # Size in bytes
    last_modified: datetime
    storage_class: str = "STANDARD"
    etag: str = ""


class ReadOnlyS3Client:
    """Read-only S3 interface using boto3.Session."""

    def __init__(self, session: Optional[boto3.Session] = None) -> None:
        self.session = session or boto3.Session()
        self._s3 = self.session.client("s3")

    def list_buckets(self) -> List[S3BucketItem]:
        """Lists accessible buckets."""
        try:
            res = self._s3.list_buckets()
            buckets = []
            for b in res.get("Buckets", []):
                buckets.append(
                    S3BucketItem(
                        name=b["Name"],
                        creation_date=b.get("CreationDate"),
                    )
                )
            return sorted(buckets, key=lambda x: x.name.lower())
        except Exception as e:
            raise RuntimeError(f"Failed to list buckets: {e}")

    def list_prefix(
        self, bucket: str, prefix: str = "", delimiter: str = "/"
    ) -> Tuple[List[S3FolderItem], List[S3FileItem]]:
        """Lists folders (CommonPrefixes) and files (Contents) in a single directory."""
        if prefix and not prefix.endswith("/"):
            prefix += "/"

        paginator = self._s3.get_paginator("list_objects_v2")
        folders: List[S3FolderItem] = []
        files: List[S3FileItem] = []

        try:
            page_iterator = paginator.paginate(
                Bucket=bucket,
                Prefix=prefix,
                Delimiter=delimiter,
                PaginationConfig={"MaxItems": 20000},
            )

            for page in page_iterator:
                # Sub-directories
                for cp in page.get("CommonPrefixes", []):
                    full_p = cp["Prefix"]
                    # Extract folder display name
                    rel = full_p[len(prefix):].rstrip("/")
                    if rel:
                        folders.append(S3FolderItem(name=rel, prefix=full_p))

                # Files
                for obj in page.get("Contents", []):
                    key = obj["Key"]
                    # Skip the folder marker placeholder if present
                    if key == prefix:
                        continue
                    name = key[len(prefix):]
                    if not name:
                        continue
                    files.append(
                        S3FileItem(
                            name=name,
                            key=key,
                            size=obj.get("Size", 0),
                            last_modified=obj.get("LastModified", datetime.now(timezone.utc)),
                            storage_class=obj.get("StorageClass", "STANDARD"),
                            etag=obj.get("ETag", "").strip('"'),
                        )
                    )

            folders.sort(key=lambda x: x.name.lower())
            files.sort(key=lambda x: x.name.lower())
            return folders, files

        except Exception as e:
            raise RuntimeError(f"Failed to list s3://{bucket}/{prefix}: {e}")

    def iter_tree(
        self, bucket: str, prefix: str = ""
    ) -> Generator[S3FileItem, None, None]:
        """Pipelined streaming generator yielding all recursive keys under prefix."""
        paginator = self._s3.get_paginator("list_objects_v2")
        try:
            page_iterator = paginator.paginate(Bucket=bucket, Prefix=prefix)
            for page in page_iterator:
                for obj in page.get("Contents", []):
                    key = obj["Key"]
                    # Skip root folder marker
                    if key.endswith("/") and obj.get("Size", 0) == 0:
                        continue
                    yield S3FileItem(
                        name=key.split("/")[-1],
                        key=key,
                        size=obj.get("Size", 0),
                        last_modified=obj.get("LastModified", datetime.now(timezone.utc)),
                        storage_class=obj.get("StorageClass", "STANDARD"),
                        etag=obj.get("ETag", "").strip('"'),
                    )
        except Exception as e:
            raise RuntimeError(f"Failed scanning s3://{bucket}/{prefix}: {e}")

    # Explicitly prohibit any write methods
    def put_object(self, *args: Any, **kwargs: Any) -> None:
        raise PermissionError("Write operations are disabled in this read-only client.")

    def delete_object(self, *args: Any, **kwargs: Any) -> None:
        raise PermissionError("Delete operations are disabled in this read-only client.")


class MockReadOnlyS3Client:
    """Mock client for offline testing on Windows without AWS credentials."""

    def __init__(self) -> None:
        self._buckets = ["prod-analytics-data", "media-archive", "ml-models-2026"]
        self._mock_data: Dict[str, List[Dict[str, Any]]] = {}
        self._generate_mock_dataset()

    def _generate_mock_dataset(self) -> None:
        now = datetime.now(timezone.utc)
        # Bucket 1: prod-analytics-data
        items = []
        for year in ["2025", "2026"]:
            for month in ["01", "02", "03"]:
                prefix = f"raw_events/{year}/{month}/"
                for i in range(1, 15):
                    items.append({
                        "key": f"{prefix}events_{i:02d}.parquet",
                        "size": random.randint(5 * 1024 * 1024, 250 * 1024 * 1024),
                        "modified": now - timedelta(days=random.randint(1, 180)),
                        "class": "STANDARD",
                    })
        items.append({
            "key": "schema_definition.json",
            "size": 14200,
            "modified": now - timedelta(days=2),
            "class": "STANDARD",
        })
        self._mock_data["prod-analytics-data"] = items

        # Bucket 2: media-archive
        items2 = []
        for cat in ["audio", "video", "photos"]:
            for i in range(1, 20):
                ext = "mp4" if cat == "video" else ("flac" if cat == "audio" else "jpg")
                size = random.randint(50 * 1024 * 1024, 1500 * 1024 * 1024) if cat != "photos" else random.randint(2 * 1024 * 1024, 25 * 1024 * 1024)
                items2.append({
                    "key": f"assets/{cat}/item_{i:03d}.{ext}",
                    "size": size,
                    "modified": now - timedelta(days=random.randint(10, 500)),
                    "class": "INTELLIGENT_TIERING",
                })
        self._mock_data["media-archive"] = items2

        # Bucket 3: ml-models-2026
        items3 = []
        for model in ["transformer-v2", "resnet-v5"]:
            items3.append({
                "key": f"checkpoints/{model}/weights.bin",
                "size": 3800 * 1024 * 1024,  # 3.8 GB
                "modified": now - timedelta(days=5),
                "class": "STANDARD",
            })
            items3.append({
                "key": f"checkpoints/{model}/config.json",
                "size": 4200,
                "modified": now - timedelta(days=5),
                "class": "STANDARD",
            })
        self._mock_data["ml-models-2026"] = items3

    def list_buckets(self) -> List[S3BucketItem]:
        now = datetime.now(timezone.utc)
        return [
            S3BucketItem(name=b, creation_date=now - timedelta(days=365))
            for b in self._buckets
        ]

    def list_prefix(
        self, bucket: str, prefix: str = "", delimiter: str = "/"
    ) -> Tuple[List[S3FolderItem], List[S3FileItem]]:
        if prefix and not prefix.endswith("/"):
            prefix += "/"

        items = self._mock_data.get(bucket, [])
        folder_names = set()
        files = []

        for item in items:
            key = item["key"]
            if not key.startswith(prefix):
                continue
            suffix = key[len(prefix):]
            if not suffix:
                continue

            if "/" in suffix:
                folder_name = suffix.split("/")[0]
                folder_names.add(folder_name)
            else:
                files.append(
                    S3FileItem(
                        name=suffix,
                        key=key,
                        size=item["size"],
                        last_modified=item["modified"],
                        storage_class=item["class"],
                        etag="mock-etag-12345",
                    )
                )

        folders = [
            S3FolderItem(name=f, prefix=f"{prefix}{f}/")
            for f in sorted(folder_names)
        ]
        files.sort(key=lambda x: x.name.lower())
        return folders, files

    def iter_tree(
        self, bucket: str, prefix: str = ""
    ) -> Generator[S3FileItem, None, None]:
        items = self._mock_data.get(bucket, [])
        for item in items:
            key = item["key"]
            if key.startswith(prefix):
                yield S3FileItem(
                    name=key.split("/")[-1],
                    key=key,
                    size=item["size"],
                    last_modified=item["modified"],
                    storage_class=item["class"],
                    etag="mock-etag-12345",
                )
