"""Unit tests for SpeedCalculator, DownloadTask, and BulkDownloader."""

import shutil
import tempfile
import time
import unittest
from pathlib import Path

from s3_browser.downloader import (
    BulkDownloader,
    DownloadTask,
    SpeedCalculator,
    TaskStatus,
)
from s3_browser.s3_client import S3FileItem


class TestDownloader(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp())

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_speed_calculator(self) -> None:
        calc = SpeedCalculator(window_seconds=1.0)
        calc.record(1024 * 1024)
        time.sleep(0.05)
        calc.record(1024 * 1024)
        bps = calc.get_current_speed_bps()
        self.assertGreater(bps, 0.0)

    def test_mock_bulk_download(self) -> None:
        downloader = BulkDownloader(is_mock=True)
        updated_tasks = []
        summary_events = []

        downloader.on_task_updated = lambda t: updated_tasks.append(t.status)
        downloader.on_summary_updated = lambda s: summary_events.append(s)

        task = downloader.enqueue_file(
            bucket="mock-bucket",
            key="test/file1.csv",
            size=5000,
            destination_dir=self.temp_dir,
        )
        self.assertEqual(task.status, TaskStatus.QUEUED)
        self.assertEqual(len(downloader.tasks), 1)

        # Run queue synchronously in mock
        downloader._run_queue()

        self.assertEqual(task.status, TaskStatus.COMPLETED)
        self.assertTrue((self.temp_dir / "file1.csv").exists())
        self.assertIn(TaskStatus.COMPLETED, updated_tasks)
        self.assertTrue(len(summary_events) > 0)


if __name__ == "__main__":
    unittest.main()
