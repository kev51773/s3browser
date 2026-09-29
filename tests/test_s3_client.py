"""Unit tests for ReadOnlyS3Client and MockReadOnlyS3Client."""

import unittest

from s3_browser.s3_client import MockReadOnlyS3Client, ReadOnlyS3Client


class TestS3Client(unittest.TestCase):
    def test_mock_client_listing(self) -> None:
        client = MockReadOnlyS3Client()
        buckets = client.list_buckets()
        self.assertGreater(len(buckets), 0)
        self.assertIn("prod-analytics-data", [b.name for b in buckets])

        folders, files = client.list_prefix("prod-analytics-data", "")
        self.assertTrue(len(folders) > 0 or len(files) > 0)

        # Recursive iter_tree
        all_files = list(client.iter_tree("prod-analytics-data", "raw_events/"))
        self.assertGreater(len(all_files), 5)
        for f in all_files:
            self.assertTrue(f.key.startswith("raw_events/"))
            self.assertGreater(f.size, 0)

    def test_read_only_guarantee(self) -> None:
        client = ReadOnlyS3Client()
        with self.assertRaises(PermissionError):
            client.put_object(Bucket="b", Key="k", Body=b"test")

        with self.assertRaises(PermissionError):
            client.delete_object(Bucket="b", Key="k")


if __name__ == "__main__":
    unittest.main()
