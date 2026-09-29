"""Unit tests for config, SSO profiles, and cascading bookmarks."""

import shutil
import tempfile
import unittest
from pathlib import Path

from s3_browser.config import (
    BookmarkFolder,
    BookmarkItem,
    ConfigManager,
    SSOProfile,
)


class TestConfig(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp())
        self.mgr = ConfigManager(base_dir=self.temp_dir)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_sso_profile_crud(self) -> None:
        prof = SSOProfile(
            profile_name="work-dev",
            start_url="https://company.awsapps.com/start",
            sso_region="us-east-1",
            account_id="123456789012",
            role_name="ReadOnlyAccess",
            default_region="us-east-1",
        )
        self.mgr.add_or_update_profile(prof)

        # Reload from disk
        mgr2 = ConfigManager(base_dir=self.temp_dir)
        self.assertIn("work-dev", mgr2.profiles)
        p = mgr2.profiles["work-dev"]
        self.assertEqual(p.start_url, "https://company.awsapps.com/start")
        self.assertEqual(p.sso_region, "us-east-1")
        self.assertEqual(p.account_id, "123456789012")
        self.assertEqual(p.role_name, "ReadOnlyAccess")

        # Delete
        self.mgr.delete_profile("work-dev")
        mgr3 = ConfigManager(base_dir=self.temp_dir)
        self.assertNotIn("work-dev", mgr3.profiles)

    def test_cascading_bookmarks(self) -> None:
        # Add root bookmark
        bm1 = BookmarkItem(name="Root BM", profile="dev", path="s3://my-bucket/")
        self.mgr.add_bookmark(bm1)

        # Add bookmark in folder
        bm2 = BookmarkItem(name="Child BM", profile="prod", path="s3://prod-bucket/data/")
        self.mgr.add_bookmark(bm2, folder_name="Production Data")

        # Reload from disk
        mgr2 = ConfigManager(base_dir=self.temp_dir)
        # Find folder
        folders = [b for b in mgr2.bookmarks if isinstance(b, BookmarkFolder)]
        self.assertTrue(any(f.name == "Production Data" for f in folders))
        target_f = next(f for f in folders if f.name == "Production Data")
        self.assertEqual(len(target_f.children), 1)
        self.assertEqual(target_f.children[0].name, "Child BM")
        self.assertEqual(target_f.children[0].path, "s3://prod-bucket/data/")

    def test_move_bookmark_and_remove(self) -> None:
        folder_a = BookmarkFolder(name="Folder A")
        folder_b = BookmarkFolder(name="Folder B")
        bm = BookmarkItem(name="Item 1", profile="dev", path="s3://bucket/1")

        self.mgr.bookmarks = [folder_a, folder_b]
        folder_a.children.append(bm)
        self.mgr.save_bookmarks()

        # Move bm from folder_a to folder_b
        res = self.mgr.move_bookmark(bm, target_folder=folder_b)
        self.assertTrue(res)
        self.assertNotIn(bm, folder_a.children)
        self.assertIn(bm, folder_b.children)

        # Move bm to root
        res = self.mgr.move_bookmark(bm, target_folder=None)
        self.assertTrue(res)
        self.assertNotIn(bm, folder_b.children)
        self.assertIn(bm, self.mgr.bookmarks)

        # Test prevent folder inside itself or child
        res = self.mgr.move_bookmark(folder_a, target_folder=folder_a)
        self.assertFalse(res)

        sub_a = BookmarkFolder(name="Sub A")
        folder_a.children.append(sub_a)
        res = self.mgr.move_bookmark(folder_a, target_folder=sub_a)
        self.assertFalse(res)

        # Remove bookmark
        res = self.mgr.remove_bookmark(bm)
        self.assertTrue(res)
        self.assertNotIn(bm, self.mgr.bookmarks)


if __name__ == "__main__":
    unittest.main()

