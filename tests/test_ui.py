"""UI component smoke test using PySide6 offscreen platform."""

import os
import shutil
import tempfile
import unittest
from pathlib import Path



from PySide6.QtWidgets import QApplication

from s3_browser.config import ConfigManager
from s3_browser.ui.main_window import MainWindow


class TestUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp())
        self.config_manager = ConfigManager(base_dir=self.temp_dir)
        self.config_manager.settings.demo_mode = True

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_main_window_instantiation(self) -> None:
        window = MainWindow(config_manager=self.config_manager, allow_demo=True)
        window.show()
        self.assertIsNotNone(window)
        self.assertEqual(window.windowTitle(), "S3 Browser")
        self.assertTrue(window.allow_demo)
        self.assertTrue(window.chk_demo.isVisible())

        # Test cascading bookmarks menu
        self.assertIsNotNone(window.bookmarks_menu)
        actions = window.bookmarks_menu.actions()
        self.assertTrue(len(actions) >= 3)  # Add, Organize, Separator, Examples

        # Test navigation to mock bucket
        window.navigate_to("prod-analytics-data", "raw_events/")
        self.assertEqual(window.current_bucket, "prod-analytics-data")
        self.assertEqual(window.current_prefix, "raw_events/")

        import time
        start = time.time()
        while time.time() - start < 1.0 and window.table_model.rowCount() == 0:
            QApplication.processEvents()
            time.sleep(0.05)

        window.close()

    def test_demo_mode_hidden_without_flag(self) -> None:
        window = MainWindow(config_manager=self.config_manager, allow_demo=False)
        window.show()
        self.assertFalse(window.allow_demo)
        self.assertFalse(window.chk_demo.isVisible())
        window.close()

    def test_add_bookmark_dialog_preselects_profile(self) -> None:
        from s3_browser.ui.dialogs.bookmark_dialog import AddBookmarkDialog

        # Test with Demo mode profile
        dlg = AddBookmarkDialog(
            config_manager=self.config_manager,
            current_profile="Demo",
            current_path="s3://prod-analytics-data/raw_events/",
        )
        self.assertEqual(dlg.combo_profile.currentText(), "Demo")
        self.assertEqual(dlg.edit_path.text(), "s3://prod-analytics-data/raw_events/")

        # Test with specific configured profile
        from s3_browser.config import SSOProfile
        self.config_manager.add_or_update_profile(
            SSOProfile("prod-analytics", "https://url", "us-east-1", "123", "role")
        )
        dlg2 = AddBookmarkDialog(
            config_manager=self.config_manager,
            current_profile="prod-analytics",
            current_path="s3://my-bucket/models/",
        )
        self.assertEqual(dlg2.combo_profile.currentText(), "prod-analytics")

    def test_tree_node_expansion_loads_subfolders(self) -> None:
        import time
        from PySide6.QtCore import Qt

        window = MainWindow(config_manager=self.config_manager, allow_demo=True)
        window.show()

        model = window.tree_model
        # Wait for buckets to load from background thread
        start = time.time()
        while time.time() - start < 2.0 and model.rowCount() == 0:
            QApplication.processEvents()
            time.sleep(0.05)

        # Find media-archive bucket in tree
        media_item = None
        for r in range(model.rowCount()):
            item = model.item(r)
            if item.text() == "media-archive":
                media_item = item
                break

        self.assertIsNotNone(media_item, "media-archive bucket item should exist")

        # Initially child is dummy "Loading..."
        self.assertEqual(media_item.rowCount(), 1)
        self.assertEqual(media_item.child(0).text(), "Loading...")

        # Trigger expansion
        window._on_tree_node_expanded(media_item.index())

        # Wait briefly for worker thread to complete
        start = time.time()
        while time.time() - start < 2.0:
            QApplication.processEvents()
            child_0 = media_item.child(0)
            if media_item.rowCount() > 0 and child_0 and child_0.text() != "Loading...":
                break
            time.sleep(0.05)

        # Verify child is "assets" folder, not "Loading..."
        self.assertIsNotNone(media_item.child(0))
        self.assertEqual(media_item.child(0).text(), "assets")
        window.close()

    def test_tree_selection_syncs_on_navigate_up(self) -> None:
        import time
        window = MainWindow(config_manager=self.config_manager, allow_demo=True)
        window.show()

        start = time.time()
        while time.time() - start < 2.0 and window.tree_model.rowCount() == 0:
            QApplication.processEvents()
            time.sleep(0.05)

        # Navigate into subfolder
        window.navigate_to("prod-analytics-data", "raw_events/")
        start = time.time()
        while time.time() - start < 1.0 and window.table_model.rowCount() == 0:
            QApplication.processEvents()
            time.sleep(0.05)

        # Navigate Up to bucket root
        window._navigate_up()
        self.assertEqual(window.current_prefix, "")
        root_idx = window.tree_view.currentIndex()
        self.assertTrue(root_idx.isValid())
        root_item = window.tree_model.itemFromIndex(root_idx)
        self.assertEqual(root_item.text(), "prod-analytics-data")
        window.close()

    def test_organize_bookmarks_dialog(self) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QTreeWidgetItem
        from s3_browser.config import BookmarkFolder, BookmarkItem
        from s3_browser.ui.dialogs.bookmark_dialog import OrganizeBookmarksDialog

        folder = BookmarkFolder(name="Team A")
        bm = BookmarkItem(name="Log Bucket", profile="Demo", path="s3://team-a-logs/")
        folder.children.append(bm)
        self.config_manager.bookmarks = [folder]

        dlg = OrganizeBookmarksDialog(config_manager=self.config_manager)
        dlg.show()

        # Check tree populated
        self.assertEqual(dlg.tree.topLevelItemCount(), 1)
        top_item = dlg.tree.topLevelItem(0)
        self.assertEqual(top_item.text(0), "Team A")
        self.assertEqual(top_item.childCount(), 1)
        self.assertEqual(top_item.child(0).text(0), "Log Bucket")

        # Move bookmark to root using config_manager and verify tree update
        self.config_manager.move_bookmark(bm, target_folder=None)
        dlg._populate_tree()
        self.assertEqual(dlg.tree.topLevelItemCount(), 2)

        # Test rebuild from tree
        # Simulate moving Log Bucket back inside Team A in the tree
        folder_item = dlg.tree.topLevelItem(0)
        dlg.tree.takeTopLevelItem(1)
        new_child = QTreeWidgetItem([bm.name, bm.profile, bm.path])
        new_child.setData(0, Qt.UserRole, bm)
        folder_item.addChild(new_child)

        dlg._rebuild_from_tree()

        # Verify config reflects tree hierarchy
        self.assertEqual(len(self.config_manager.bookmarks), 1)
        self.assertIsInstance(self.config_manager.bookmarks[0], BookmarkFolder)
        self.assertEqual(len(self.config_manager.bookmarks[0].children), 1)
        self.assertEqual(self.config_manager.bookmarks[0].children[0].name, "Log Bucket")

        dlg.close()


if __name__ == "__main__":
    unittest.main()

