"""High-speed bulk downloader engine using boto3 and s3transfer.

Supports:
- Saturated multi-threaded downloads (20-50 worker threads)
- Parallel chunked multipart downloads for large files
- Streaming directory downloads without waiting for pagination to finish
- Real-time throughput (MB/s), ETA, and aggregate progress tracking
- Offline simulation mode for development on Windows
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import boto3
from s3transfer.manager import TransferConfig, TransferManager


class TaskStatus(str, Enum):
    QUEUED = "Queued"
    DOWNLOADING = "Downloading"
    COMPLETED = "Completed"
    FAILED = "Failed"
    SKIPPED = "Skipped"
    CANCELLED = "Cancelled"


@dataclass
class DownloadTask:
    task_id: str
    bucket: str
    key: str
    local_path: Path
    total_bytes: int
    downloaded_bytes: int = 0
    status: TaskStatus = TaskStatus.QUEUED
    speed_bps: float = 0.0
    eta_seconds: float = 0.0
    error_message: str = ""

    @property
    def filename(self) -> str:
        return self.local_path.name

    @property
    def progress_pct(self) -> float:
        if self.total_bytes <= 0:
            return 100.0 if self.status == TaskStatus.COMPLETED else 0.0
        return min(100.0, (self.downloaded_bytes / self.total_bytes) * 100.0)


class SpeedCalculator:
    """Thread-safe sliding window throughput calculator."""

    def __init__(self, window_seconds: float = 2.0) -> None:
        self.window_seconds = window_seconds
        self.samples: deque[tuple[float, int]] = deque()
        self.total_transferred = 0
        self._lock = threading.Lock()

    def record(self, byte_count: int) -> None:
        with self._lock:
            now = time.time()
            self.total_transferred += byte_count
            self.samples.append((now, byte_count))
            cutoff = now - self.window_seconds
            while self.samples and self.samples[0][0] < cutoff:
                self.samples.popleft()

    def get_current_speed_bps(self) -> float:
        with self._lock:
            now = time.time()
            cutoff = now - self.window_seconds
            while self.samples and self.samples[0][0] < cutoff:
                self.samples.popleft()

            if len(self.samples) < 2:
                return 0.0

            time_delta = self.samples[-1][0] - self.samples[0][0]
            if time_delta <= 0:
                return 0.0

            bytes_sum = sum(b for _, b in self.samples)
            return bytes_sum / time_delta


class BulkDownloader:
    """Orchestrates parallel file and directory downloads."""

    def __init__(
        self,
        boto3_session: Optional[boto3.Session] = None,
        max_concurrency: int = 30,
        multipart_chunksize_mb: int = 16,
        is_mock: bool = False,
    ) -> None:
        self.session = boto3_session or boto3.Session()
        self.is_mock = is_mock
        self.max_concurrency = max_concurrency
        self.chunksize = multipart_chunksize_mb * 1024 * 1024

        self.tasks: Dict[str, DownloadTask] = {}
        self.tasks_order: List[str] = []
        self.speed_calc = SpeedCalculator()

        self._transfer_manager: Optional[TransferManager] = None
        self._cancel_requested = False
        self._lock = threading.Lock()

        # Callbacks: on_task_updated(task), on_all_progress(summary_dict)
        self.on_task_updated: Optional[Callable[[DownloadTask], None]] = None
        self.on_summary_updated: Optional[Callable[[Dict[str, Any]], None]] = None

        if not self.is_mock:
            self._init_transfer_manager()

    def _init_transfer_manager(self) -> None:
        client = self.session.client("s3")
        config = TransferConfig(
            max_request_concurrency=self.max_concurrency,
            multipart_threshold=self.chunksize,
            multipart_chunksize=self.chunksize,
            max_submission_concurrency=100,
        )
        self._transfer_manager = TransferManager(client, config=config)

    def enqueue_file(
        self, bucket: str, key: str, size: int, destination_dir: Path
    ) -> DownloadTask:
        """Enqueues a single file download."""
        # Clean relative path inside bucket prefix
        rel_path = key.split("/")[-1]
        target_path = destination_dir / rel_path
        task_id = f"{bucket}/{key}"

        with self._lock:
            if task_id in self.tasks:
                return self.tasks[task_id]

            task = DownloadTask(
                task_id=task_id,
                bucket=bucket,
                key=key,
                local_path=target_path,
                total_bytes=size,
            )
            self.tasks[task_id] = task
            self.tasks_order.append(task_id)

        if self.on_task_updated:
            self.on_task_updated(task)
        self._emit_summary()
        return task

    def enqueue_tree(
        self,
        bucket: str,
        base_prefix: str,
        file_iterator: Any,
        destination_dir: Path,
    ) -> None:
        """Streams a recursive directory tree into download tasks immediately."""
        base_prefix_clean = base_prefix.rstrip("/")
        prefix_len = len(base_prefix_clean) + 1 if base_prefix_clean else 0

        for file_item in file_iterator:
            if self._cancel_requested:
                break
            key = file_item.key
            # Retain directory hierarchy relative to base_prefix
            rel = key[prefix_len:] if prefix_len and key.startswith(base_prefix_clean) else key.split("/")[-1]
            target_path = destination_dir / Path(rel)
            task_id = f"{bucket}/{key}"

            with self._lock:
                if task_id not in self.tasks:
                    task = DownloadTask(
                        task_id=task_id,
                        bucket=bucket,
                        key=key,
                        local_path=target_path,
                        total_bytes=file_item.size,
                    )
                    self.tasks[task_id] = task
                    self.tasks_order.append(task_id)
                    if self.on_task_updated:
                        self.on_task_updated(task)

            self._emit_summary()

    def start(self) -> None:
        """Starts processing the download queue in background worker threads."""
        self._cancel_requested = False
        t = threading.Thread(target=self._run_queue, daemon=True)
        t.start()

    def cancel_all(self) -> None:
        """Cancels all pending and in-flight downloads."""
        self._cancel_requested = True
        with self._lock:
            for task in self.tasks.values():
                if task.status in (TaskStatus.QUEUED, TaskStatus.DOWNLOADING):
                    task.status = TaskStatus.CANCELLED
                    if self.on_task_updated:
                        self.on_task_updated(task)
        self._emit_summary()

    def _run_queue(self) -> None:
        queued_ids = [
            tid for tid in self.tasks_order
            if self.tasks[tid].status == TaskStatus.QUEUED
        ]

        if self.is_mock:
            self._run_mock_queue(queued_ids)
            return

        futures = []
        for tid in queued_ids:
            if self._cancel_requested:
                break
            task = self.tasks[tid]
            task.local_path.parent.mkdir(parents=True, exist_ok=True)

            # Skip check
            if task.local_path.exists() and task.local_path.stat().st_size == task.total_bytes and task.total_bytes > 0:
                task.status = TaskStatus.SKIPPED
                task.downloaded_bytes = task.total_bytes
                if self.on_task_updated:
                    self.on_task_updated(task)
                continue

            task.status = TaskStatus.DOWNLOADING
            if self.on_task_updated:
                self.on_task_updated(task)

            callback = self._make_progress_callback(task)
            future = self._transfer_manager.download(
                bucket=task.bucket,
                key=task.key,
                fileobj=str(task.local_path),
                subscribers=[callback] if callback else None,
            )
            futures.append((future, task))

        # Wait for all futures
        for future, task in futures:
            try:
                future.result()
                if task.status == TaskStatus.DOWNLOADING:
                    task.status = TaskStatus.COMPLETED
                    task.downloaded_bytes = task.total_bytes
            except Exception as e:
                task.status = TaskStatus.FAILED
                task.error_message = str(e)

            if self.on_task_updated:
                self.on_task_updated(task)
            self._emit_summary()

    def _make_progress_callback(self, task: DownloadTask) -> Any:
        from s3transfer.subscribers import BaseSubscriber

        parent = self

        class ChunkSubscriber(BaseSubscriber):
            def on_progress(self, future: Any, bytes_transferred: int, **kwargs: Any) -> None:
                parent._on_bytes_received(task, bytes_transferred)

        return ChunkSubscriber()

    def _on_bytes_received(self, task: DownloadTask, bytes_count: int) -> None:
        task.downloaded_bytes += bytes_count
        self.speed_calc.record(bytes_count)
        bps = self.speed_calc.get_current_speed_bps()
        task.speed_bps = bps
        rem_bytes = max(0, task.total_bytes - task.downloaded_bytes)
        task.eta_seconds = (rem_bytes / bps) if bps > 0 else 0.0

        if self.on_task_updated:
            self.on_task_updated(task)
        self._emit_summary()

    def _run_mock_queue(self, queued_ids: List[str]) -> None:
        """Simulates rapid multi-threaded bulk downloading for offline testing."""
        for tid in queued_ids:
            if self._cancel_requested:
                break
            task = self.tasks[tid]
            task.local_path.parent.mkdir(parents=True, exist_ok=True)
            task.status = TaskStatus.DOWNLOADING
            if self.on_task_updated:
                self.on_task_updated(task)

            # Simulated chunks
            chunk_size = max(1024 * 1024, task.total_bytes // 10)
            transferred = 0
            while transferred < task.total_bytes:
                if self._cancel_requested:
                    task.status = TaskStatus.CANCELLED
                    break
                step = min(chunk_size, task.total_bytes - transferred)
                time.sleep(0.04)  # ~25 MB/s simulation per file
                transferred += step
                self._on_bytes_received(task, step)

            if not self._cancel_requested:
                task.status = TaskStatus.COMPLETED
                task.downloaded_bytes = task.total_bytes
                # Write dummy file on disk for realistic test
                with open(task.local_path, "wb") as f:
                    f.write(b"0" * min(1024, task.total_bytes))

            if self.on_task_updated:
                self.on_task_updated(task)
            self._emit_summary()

    def _emit_summary(self) -> None:
        if not self.on_summary_updated:
            return

        with self._lock:
            total_tasks = len(self.tasks)
            completed_tasks = sum(1 for t in self.tasks.values() if t.status in (TaskStatus.COMPLETED, TaskStatus.SKIPPED))
            total_bytes = sum(t.total_bytes for t in self.tasks.values())
            downloaded_bytes = sum(t.downloaded_bytes for t in self.tasks.values())
            bps = self.speed_calc.get_current_speed_bps()
            rem_bytes = max(0, total_bytes - downloaded_bytes)
            eta = (rem_bytes / bps) if bps > 0 else 0.0

        summary = {
            "total_files": total_tasks,
            "completed_files": completed_tasks,
            "total_bytes": total_bytes,
            "downloaded_bytes": downloaded_bytes,
            "speed_bps": bps,
            "eta_seconds": eta,
            "progress_pct": (downloaded_bytes / total_bytes * 100.0) if total_bytes > 0 else 0.0,
        }
        self.on_summary_updated(summary)
