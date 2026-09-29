"""Main Window replicating S3 Browser layout with cascading Bookmarks menu."""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Any, List, Optional, Tuple

from PySide6.QtCore import (
    QItemSelectionModel,
    QModelIndex,
    QPersistentModelIndex,
    Qt,
    Signal,
)
from PySide6.QtGui import QAction, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStatusBar,
    QToolBar,
    QTreeView,
    QVBoxLayout,
    QWidget,
)
import qtawesome as qta

from s3_browser.auth import SSOAuthManager
from s3_browser.config import (
    BookmarkFolder,
    BookmarkItem,
    ConfigManager,
    SSOProfile,
)
from s3_browser.downloader import BulkDownloader, DownloadTask
from s3_browser.s3_client import (
    MockReadOnlyS3Client,
    ReadOnlyS3Client,
    S3BucketItem,
    S3FileItem,
    S3FolderItem,
)
from s3_browser.ui.dialogs.bookmark_dialog import (
    AddBookmarkDialog,
    OrganizeBookmarksDialog,
)
from s3_browser.ui.dialogs.profile_dialog import ProfileManagerDialog
from s3_browser.ui.models import (
    S3FolderTreeModel,
    S3RowItem,
    S3TableModel,
    format_bytes,
)
from s3_browser.ui.transfer_panel import TransferPanel
from s3_browser.ui.views import S3TableView


class MainWindow(QMainWindow):
    """S3 Browser Main Window with cascading bookmarks and high-speed downloader."""

    # Thread-safe Qt signals
    s3_data_loaded = Signal(object, object)  # (folders, files)
    buckets_loaded = Signal(list)            # [S3BucketItem]
    tree_subfolders_loaded = Signal(QPersistentModelIndex, str, list)  # (p_idx, bucket, folders)
    task_progress = Signal(object)           # DownloadTask
    summary_progress = Signal(dict)          # summary dict
    status_message = Signal(str)

    def __init__(self, config_manager: ConfigManager, allow_demo: bool = False) -> None:
        super().__init__()
        self.setWindowTitle("S3 Browser")
        self.resize(1180, 780)
        self.config_manager = config_manager
        self.allow_demo = allow_demo

        # Current navigation state
        self.current_bucket = ""
        self.current_prefix = ""
        self.current_profile_name = ""
        self._pending_navigation: Optional[Tuple[str, str]] = None

        # S3 Client & Downloader
        self.s3_client: Any = None
        self.downloader: Optional[BulkDownloader] = None

        self._init_models()
        self._init_ui()
        self._init_signals()
        self._init_menu_bar()

        # Load initial profile or activate demo mode
        self._bootstrap_initial_connection()

    def _init_models(self) -> None:
        self.tree_model = S3FolderTreeModel(self)
        self.table_model = S3TableModel(self)

    def _init_ui(self) -> None:
        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)
        root_layout = QVBoxLayout(central_widget)
        root_layout.setContentsMargins(6, 6, 6, 6)
        root_layout.setSpacing(6)

        # 1. Top Session Bar: Profile selector, Demo mode, and Action buttons
        session_bar = QHBoxLayout()
        session_bar.setSpacing(8)

        lbl_profile = QLabel("Profile:")
        self.combo_profiles = QComboBox()
        self.combo_profiles.setMinimumWidth(180)
        self.combo_profiles.currentIndexChanged.connect(self._on_profile_combo_changed)

        self.btn_manage_profiles = QPushButton(qta.icon("fa5s.user-cog"), "Profiles...")
        self.btn_manage_profiles.clicked.connect(self._open_profile_manager)

        self.chk_demo = QCheckBox("Offline Demo Mode")
        self.chk_demo.setChecked(self.allow_demo)
        self.chk_demo.setVisible(self.allow_demo)
        self.chk_demo.toggled.connect(self._on_demo_toggled)

        session_bar.addWidget(lbl_profile)
        session_bar.addWidget(self.combo_profiles)
        session_bar.addWidget(self.btn_manage_profiles)
        if self.allow_demo:
            session_bar.addWidget(self.chk_demo)
        session_bar.addStretch()

        self.btn_download_selected = QPushButton(qta.icon("fa5s.download", color="#27ae60"), "Download (Cmd+D)")
        self.btn_download_selected.setStyleSheet("font-weight: bold; padding: 4px 14px;")
        self.btn_download_selected.clicked.connect(self._download_selected_rows)
        session_bar.addWidget(self.btn_download_selected)

        root_layout.addLayout(session_bar)

        # 2. Navigation / Location Bar: Up, Refresh, Path address, and Filter
        nav_bar = QHBoxLayout()
        nav_bar.setSpacing(6)

        self.btn_up = QPushButton(qta.icon("fa5s.arrow-up"), "")
        self.btn_up.setToolTip("Up to parent folder (Backspace / Cmd+Up)")
        self.btn_up.setFixedWidth(34)
        self.btn_up.clicked.connect(self._navigate_up)

        self.btn_refresh = QPushButton(qta.icon("fa5s.sync-alt"), "")
        self.btn_refresh.setToolTip("Refresh (Cmd+R / Ctrl+R)")
        self.btn_refresh.setFixedWidth(34)
        self.btn_refresh.clicked.connect(self._refresh_current_view)

        lbl_path = QLabel("Path:")
        self.edit_address = QLineEdit()
        self.edit_address.setPlaceholderText("s3://bucket-name/folder/path/")
        self.edit_address.returnPressed.connect(self._on_address_entered)

        # Quick client-side filter
        self.edit_filter = QLineEdit()
        self.edit_filter.setPlaceholderText("Filter files...")
        self.edit_filter.setClearButtonEnabled(True)
        self.edit_filter.setMinimumWidth(160)
        self.edit_filter.setMaximumWidth(220)
        self.edit_filter.textChanged.connect(self.table_model.set_filter)

        nav_bar.addWidget(self.btn_up)
        nav_bar.addWidget(self.btn_refresh)
        nav_bar.addWidget(lbl_path)
        nav_bar.addWidget(self.edit_address, stretch=1)
        nav_bar.addWidget(self.edit_filter)

        root_layout.addLayout(nav_bar)

        # 3. Main Dual Pane Splitter (Left: Tree, Right: Table)
        self.h_splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left pane: Filter bar + Tree View
        left_pane = QWidget()
        left_layout = QVBoxLayout(left_pane)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(4)

        self.edit_bucket_filter = QLineEdit()
        self.edit_bucket_filter.setPlaceholderText("Filter buckets...")
        self.edit_bucket_filter.setClearButtonEnabled(True)
        self.edit_bucket_filter.textChanged.connect(self._apply_bucket_filter)

        self.tree_view = QTreeView()
        self.tree_view.setModel(self.tree_model)
        self.tree_view.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree_view.clicked.connect(self._on_tree_node_clicked)
        self.tree_view.expanded.connect(self._on_tree_node_expanded)

        left_layout.addWidget(self.edit_bucket_filter)
        left_layout.addWidget(self.tree_view)
        left_pane.setMinimumWidth(200)

        self.table_view = S3TableView()
        self.table_view.setModel(self.table_model)
        self.table_view.setMinimumWidth(400)
        self.table_view.folder_double_clicked.connect(self._on_folder_opened)
        self.table_view.download_requested.connect(self._download_selected_rows)
        self.table_view.navigate_up_requested.connect(self._navigate_up)

        self.h_splitter.addWidget(left_pane)
        self.h_splitter.addWidget(self.table_view)
        self.h_splitter.setChildrenCollapsible(False)
        self.h_splitter.setStretchFactor(0, 1)
        self.h_splitter.setStretchFactor(1, 3)
        self.h_splitter.setSizes([260, 920])

        # 4. Vertical Splitter (Top: Explorer, Bottom: Transfer Manager)
        self.v_splitter = QSplitter(Qt.Orientation.Vertical)
        self.v_splitter.addWidget(self.h_splitter)

        self.transfer_panel = TransferPanel(
            default_download_dir=self.config_manager.settings.default_download_dir
        )
        self.transfer_panel.destination_changed.connect(self._on_download_dir_changed)
        self.transfer_panel.cancel_requested.connect(self._cancel_all_downloads)
        self.v_splitter.addWidget(self.transfer_panel)
        self.v_splitter.setChildrenCollapsible(False)
        self.v_splitter.setStretchFactor(0, 3)
        self.v_splitter.setStretchFactor(1, 1)
        self.v_splitter.setSizes([540, 180])

        root_layout.addWidget(self.v_splitter)

        # Status Bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.lbl_selection_info = QLabel("0 items selected")
        self.status_bar.addPermanentWidget(self.lbl_selection_info)

        # Table selection changed
        self.table_view.selectionModel().selectionChanged.connect(self._update_selection_info)

    def _init_signals(self) -> None:
        self.buckets_loaded.connect(self._handle_buckets_loaded)
        self.tree_subfolders_loaded.connect(self._handle_tree_subfolders_loaded)
        self.s3_data_loaded.connect(self._handle_s3_data_loaded)
        self.task_progress.connect(self.transfer_panel.update_task)
        self.summary_progress.connect(self.transfer_panel.update_summary)
        self.status_message.connect(self.status_bar.showMessage)

    def _init_menu_bar(self) -> None:
        menubar = self.menuBar()

        # File Menu
        file_menu = menubar.addMenu("&File")

        action_profiles = QAction("Manage SSO Profiles...", self)
        action_profiles.triggered.connect(self._open_profile_manager)
        file_menu.addAction(action_profiles)

        action_set_dest = QAction("Set Download Destination...", self)
        action_set_dest.triggered.connect(self.transfer_panel._choose_destination)
        file_menu.addAction(action_set_dest)

        file_menu.addSeparator()

        action_exit = QAction("Exit", self)
        action_exit.setShortcut("Ctrl+Q")
        action_exit.triggered.connect(self.close)
        file_menu.addAction(action_exit)

        # View Menu
        view_menu = menubar.addMenu("&View")

        action_refresh = QAction("Refresh", self)
        action_refresh.setShortcut(QKeySequence("Ctrl+R"))
        action_refresh.triggered.connect(self._refresh_current_view)
        view_menu.addAction(action_refresh)

        action_up = QAction("Up One Level", self)
        action_up.setShortcut(QKeySequence("Backspace"))
        action_up.triggered.connect(self._navigate_up)
        view_menu.addAction(action_up)

        # Bookmarks Menu (Cascading!)
        self.bookmarks_menu = menubar.addMenu("&Bookmarks")
        self._rebuild_bookmarks_menu()

        # Transfers Menu
        transfer_menu = menubar.addMenu("&Transfers")

        action_cancel_all = QAction("Cancel All Transfers", self)
        action_cancel_all.triggered.connect(self._cancel_all_downloads)
        transfer_menu.addAction(action_cancel_all)

        action_open_dl = QAction("Open Download Folder", self)
        action_open_dl.triggered.connect(self.transfer_panel._open_destination_in_system)
        transfer_menu.addAction(action_open_dl)

    def _rebuild_bookmarks_menu(self) -> None:
        """Reconstructs the cascading Bookmarks menu from config."""
        self.bookmarks_menu.clear()

        action_add_bm = QAction(qta.icon("fa5s.bookmark"), "Add Bookmark... (Cmd+B)", self)
        action_add_bm.setShortcut(QKeySequence("Ctrl+B"))
        action_add_bm.triggered.connect(self._open_add_bookmark_dialog)
        self.bookmarks_menu.addAction(action_add_bm)

        action_organize_bm = QAction(qta.icon("fa5s.tasks"), "Organize Bookmarks...", self)
        action_organize_bm.triggered.connect(self._open_organize_bookmarks_dialog)
        self.bookmarks_menu.addAction(action_organize_bm)

        self.bookmarks_menu.addSeparator()

        # Build cascading submenus
        for item in self.config_manager.bookmarks:
            if isinstance(item, BookmarkFolder):
                sub_menu = self._build_cascade_folder_menu(item)
                self.bookmarks_menu.addMenu(sub_menu)
            elif isinstance(item, BookmarkItem):
                action = self._create_bookmark_action(item)
                self.bookmarks_menu.addAction(action)

    def _build_cascade_folder_menu(self, folder: BookmarkFolder) -> QMenu:
        menu = QMenu(folder.name, self)
        menu.setIcon(qta.icon("fa5s.folder", color="#3498db"))
        for child in folder.children:
            if isinstance(child, BookmarkFolder):
                menu.addMenu(self._build_cascade_folder_menu(child))
            elif isinstance(child, BookmarkItem):
                menu.addAction(self._create_bookmark_action(child))
        return menu

    def _create_bookmark_action(self, bm: BookmarkItem) -> QAction:
        text = f"{bm.name}  ({bm.profile})"
        action = QAction(qta.icon("fa5s.bookmark", color="#f39c12"), text, self)
        action.setToolTip(f"Go to {bm.path} using profile {bm.profile}")
        action.triggered.connect(lambda: self._activate_bookmark(bm))
        return action

    def _activate_bookmark(self, bm: BookmarkItem) -> None:
        """Switches profile and opens the target S3 path directly."""
        self.status_message.emit(f"Opening bookmark: {bm.name} -> {bm.path}")

        # Parse S3 URI
        path = bm.path.strip()
        if path.startswith("s3://"):
            path = path[5:]
        path = path.lstrip("/")
        parts = path.split("/", 1)
        bucket = parts[0]
        prefix = parts[1] if len(parts) > 1 else ""
        if prefix and not prefix.endswith("/"):
            prefix += "/"

        # Determine if profile switch is required
        needs_switch = False
        target_profile = (bm.profile or "").strip()

        if target_profile.lower() == "demo":
            if not self.chk_demo.isChecked():
                needs_switch = True
                self._pending_navigation = (bucket, prefix)
                self.chk_demo.setChecked(True)
        elif target_profile:
            if self.chk_demo.isChecked():
                needs_switch = True
                self._pending_navigation = (bucket, prefix)
                self.chk_demo.setChecked(False)
            idx = self.combo_profiles.findText(target_profile)
            if idx >= 0 and self.combo_profiles.currentIndex() != idx:
                needs_switch = True
                self._pending_navigation = (bucket, prefix)
                self.combo_profiles.setCurrentIndex(idx)

        if not needs_switch:
            self._pending_navigation = None
            self.navigate_to(bucket, prefix)

    def _open_add_bookmark_dialog(self) -> None:
        current_path = f"s3://{self.current_bucket}/{self.current_prefix}" if self.current_bucket else "s3://"
        active_profile = "Demo" if self.chk_demo.isChecked() else (self.current_profile_name or self.combo_profiles.currentText() or "Default")
        dlg = AddBookmarkDialog(
            config_manager=self.config_manager,
            current_profile=active_profile,
            current_path=current_path,
            parent=self,
        )
        if dlg.exec():
            self._rebuild_bookmarks_menu()

    def _open_organize_bookmarks_dialog(self) -> None:
        dlg = OrganizeBookmarksDialog(config_manager=self.config_manager, parent=self)
        dlg.bookmarks_changed.connect(self._rebuild_bookmarks_menu)
        dlg.exec()
        self._rebuild_bookmarks_menu()

    def _open_profile_manager(self) -> None:
        dlg = ProfileManagerDialog(config_manager=self.config_manager, parent=self)
        dlg.profile_changed.connect(self._reload_profile_list)
        dlg.exec()
        self._reload_profile_list()

    def _reload_profile_list(self) -> None:
        current = self.combo_profiles.currentText()
        self.combo_profiles.blockSignals(True)
        self.combo_profiles.clear()
        for p in sorted(self.config_manager.profiles.keys()):
            self.combo_profiles.addItem(p)
        if current in self.config_manager.profiles:
            self.combo_profiles.setCurrentText(current)
        elif self.combo_profiles.count() > 0:
            self.combo_profiles.setCurrentIndex(0)
        self.combo_profiles.blockSignals(False)

        if self.combo_profiles.count() > 0:
            self._on_profile_combo_changed(self.combo_profiles.currentIndex())

    def _bootstrap_initial_connection(self) -> None:
        self._reload_profile_list()

        # If launched with --demo, use mock client
        if self.allow_demo:
            self.chk_demo.setChecked(True)
            self._init_s3_client(use_mock=True)
        elif self.combo_profiles.count() > 0:
            self.chk_demo.setChecked(False)
            self._init_s3_client(use_mock=False)
        else:
            self.chk_demo.setChecked(False)
            self.status_message.emit("No AWS SSO profiles configured. Click 'Profiles...' to add one.")

    def _on_demo_toggled(self, checked: bool) -> None:
        self.config_manager.settings.demo_mode = checked
        self.config_manager.save_settings()
        self._init_s3_client(use_mock=checked)

    def _on_profile_combo_changed(self, index: int) -> None:
        if index < 0 or self.chk_demo.isChecked():
            return
        name = self.combo_profiles.itemText(index)
        self.current_profile_name = name
        self._init_s3_client(use_mock=False)

    def _init_s3_client(self, use_mock: bool) -> None:
        if use_mock:
            self.current_profile_name = "Demo"
            self.status_message.emit("Active in Offline Mock Mode (Demo dataset loaded).")
            self.s3_client = MockReadOnlyS3Client()
            self._init_downloader(is_mock=True)
            self._load_buckets_async()
            return

        profile_name = self.combo_profiles.currentText()
        prof = self.config_manager.profiles.get(profile_name)
        if not prof:
            self.status_message.emit("No SSO profile selected. Please configure a profile.")
            return

        self.status_message.emit(f"Connecting to AWS SSO for profile '{profile_name}'...")

        def connect() -> None:
            try:
                auth = SSOAuthManager(prof)
                session = auth.create_boto3_session()
                self.s3_client = ReadOnlyS3Client(session=session)
                self._init_downloader(session=session, is_mock=False)
                self.status_message.emit(f"Connected to AWS ({profile_name}).")
                self._load_buckets_async()
            except Exception as e:
                self.status_message.emit(f"AWS Connection error: {e}")

        threading.Thread(target=connect, daemon=True).start()

    def _init_downloader(self, session: Optional[Any] = None, is_mock: bool = False) -> None:
        self.downloader = BulkDownloader(
            boto3_session=session,
            max_concurrency=self.config_manager.settings.max_concurrency,
            multipart_chunksize_mb=self.config_manager.settings.multipart_chunksize_mb,
            is_mock=is_mock,
        )
        self.downloader.on_task_updated = self.task_progress.emit
        self.downloader.on_summary_updated = self.summary_progress.emit

    def _load_buckets_async(self) -> None:
        def fetch() -> None:
            if not self.s3_client:
                return
            try:
                buckets = self.s3_client.list_buckets()
                self.buckets_loaded.emit(buckets)
            except Exception as e:
                self.status_message.emit(f"Failed to list buckets: {e}")

        threading.Thread(target=fetch, daemon=True).start()

    def navigate_to(self, bucket: str, prefix: str) -> None:
        if prefix and not prefix.endswith("/"):
            prefix += "/"
        if bucket != self.current_bucket or prefix != self.current_prefix:
            self.edit_filter.clear()
        self.current_bucket = bucket
        self.current_prefix = prefix
        self.edit_address.setText(f"s3://{bucket}/{prefix}")
        self.status_message.emit(f"Listing s3://{bucket}/{prefix}...")
        self._sync_tree_selection(bucket, prefix)

        def fetch() -> None:
            if not self.s3_client:
                return
            try:
                folders, files = self.s3_client.list_prefix(bucket, prefix)
                self.s3_data_loaded.emit(folders, files)
            except Exception as e:
                self.status_message.emit(f"Error listing path: {e}")

        threading.Thread(target=fetch, daemon=True).start()

    def _sync_tree_selection(self, bucket: str, prefix: str) -> None:
        """Synchronizes the left tree selection to the current active bucket & prefix."""
        if not bucket:
            return
        clean_p = prefix if (not prefix or prefix.endswith("/")) else prefix + "/"

        # Find top-level bucket item
        bucket_item = None
        for r in range(self.tree_model.rowCount()):
            item = self.tree_model.item(r)
            if item and item.data(Qt.UserRole) == bucket:
                bucket_item = item
                break

        if not bucket_item:
            return

        if not clean_p:
            # Root bucket selected
            self.tree_view.setRowHidden(bucket_item.row(), QModelIndex(), False)
            self.tree_view.blockSignals(True)
            self.tree_view.setCurrentIndex(bucket_item.index())
            self.tree_view.scrollTo(bucket_item.index())
            if self.tree_view.selectionModel():
                self.tree_view.selectionModel().select(
                    bucket_item.index(),
                    QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows,
                )
            self.tree_view.blockSignals(False)
            return

        # If bucket item hasn't loaded its children yet, load & expand it
        if not bucket_item.data(Qt.UserRole + 3):
            self._load_tree_subfolders_async(bucket_item, bucket, "")
            self.tree_view.expand(bucket_item.index())
            return

        # Recursive search down loaded children
        def search_children(parent_item: Any) -> Optional[Any]:
            for r in range(parent_item.rowCount()):
                child = parent_item.child(r)
                if not child or child.text() == "Loading...":
                    continue
                child_p = child.data(Qt.UserRole + 1) or ""
                if child_p and not child_p.endswith("/"):
                    child_p += "/"

                if child_p == clean_p:
                    return child

                if clean_p.startswith(child_p):
                    # Path leads through this child
                    if not child.data(Qt.UserRole + 3):
                        self._load_tree_subfolders_async(child, bucket, child_p)
                        self.tree_view.expand(child.index())
                        return None
                    return search_children(child)
            return None

        target = search_children(bucket_item)
        if target:
            # Expand and unhide parent nodes so target is visible
            p = target.parent()
            while p and p != self.tree_model.invisibleRootItem():
                p_parent_idx = p.parent().index() if p.parent() else QModelIndex()
                self.tree_view.setRowHidden(p.row(), p_parent_idx, False)
                self.tree_view.expand(p.index())
                p = p.parent()
            target_parent_idx = target.parent().index() if target.parent() else QModelIndex()
            self.tree_view.setRowHidden(target.row(), target_parent_idx, False)
            self.tree_view.blockSignals(True)
            self.tree_view.setCurrentIndex(target.index())
            self.tree_view.scrollTo(target.index())
            if self.tree_view.selectionModel():
                self.tree_view.selectionModel().select(
                    target.index(),
                    QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows,
                )
            self.tree_view.blockSignals(False)

    def _handle_s3_data_loaded(self, folders: List[S3FolderItem], files: List[S3FileItem]) -> None:
        self.table_model.set_items(folders, files)
        if self.table_view.selectionModel():
            self.table_view.selectionModel().clearSelection()
        self.table_view.scrollToTop()
        self.table_view.viewport().update()
        self.status_message.emit(f"Loaded {len(folders)} folders, {len(files)} files in s3://{self.current_bucket}/{self.current_prefix}")

    def _handle_buckets_loaded(self, buckets: List[S3BucketItem]) -> None:
        self.tree_model.populate_buckets(buckets)
        if self.edit_bucket_filter.text().strip():
            self._apply_bucket_filter()
        if self._pending_navigation:
            target_bucket, target_prefix = self._pending_navigation
            self._pending_navigation = None
            self.navigate_to(target_bucket, target_prefix)
        elif self.current_bucket:
            self._sync_tree_selection(self.current_bucket, self.current_prefix)
        elif buckets:
            self.navigate_to(buckets[0].name, "")

    def _apply_bucket_filter(self) -> None:
        query = self.edit_bucket_filter.text().strip().lower()

        def filter_item(item: QStandardItem) -> bool:
            matches_self = query in item.text().lower()
            any_child_matches = False
            for r in range(item.rowCount()):
                child = item.child(r)
                if child and child.text() != "Loading...":
                    if filter_item(child):
                        any_child_matches = True

            should_show = (not query) or matches_self or any_child_matches
            parent_idx = item.parent().index() if item.parent() else QModelIndex()
            self.tree_view.setRowHidden(item.row(), parent_idx, not should_show)
            if query and any_child_matches:
                self.tree_view.expand(item.index())
            return should_show

        for r in range(self.tree_model.rowCount()):
            item = self.tree_model.item(r)
            if item:
                filter_item(item)

    def _on_tree_node_expanded(self, index: QModelIndex) -> None:
        item = self.tree_model.itemFromIndex(index)
        if not item:
            return
        is_loaded = item.data(Qt.UserRole + 3)
        if not is_loaded:
            bucket = item.data(Qt.UserRole)
            prefix = item.data(Qt.UserRole + 1) or ""
            self._load_tree_subfolders_async(item, bucket, prefix)

    def _load_tree_subfolders_async(self, item: Any, bucket: str, prefix: str) -> None:
        p_idx = QPersistentModelIndex(item.index())
        def fetch() -> None:
            if not self.s3_client:
                return
            try:
                folders, _ = self.s3_client.list_prefix(bucket, prefix)
                self.tree_subfolders_loaded.emit(p_idx, bucket, folders)
            except Exception as e:
                self.status_message.emit(f"Error loading tree folders: {e}")

        threading.Thread(target=fetch, daemon=True).start()

    def _handle_tree_subfolders_loaded(self, p_idx: QPersistentModelIndex, bucket: str, folders: List[S3FolderItem]) -> None:
        if not p_idx.isValid():
            return
        item = self.tree_model.itemFromIndex(p_idx)
        if item:
            self.tree_model.set_subfolders(item, bucket, folders)
            if self.edit_bucket_filter.text().strip():
                self._apply_bucket_filter()
            # Re-sync tree selection now that subfolders are loaded
            if self.current_bucket == bucket:
                self._sync_tree_selection(self.current_bucket, self.current_prefix)

    def _on_tree_node_clicked(self, index: QModelIndex) -> None:
        item = self.tree_model.itemFromIndex(index)
        if not item:
            return
        bucket = item.data(Qt.UserRole)
        prefix = item.data(Qt.UserRole + 1) or ""
        self.navigate_to(bucket, prefix)

        # Pre-fetch and expand subfolders if not loaded yet
        is_loaded = item.data(Qt.UserRole + 3)
        if not is_loaded:
            self._load_tree_subfolders_async(item, bucket, prefix)
            self.tree_view.expand(index)

    def _on_folder_opened(self, prefix: str) -> None:
        self.navigate_to(self.current_bucket, prefix)

    def _on_address_entered(self) -> None:
        text = self.edit_address.text().strip()
        if text.startswith("s3://"):
            text = text[5:]
        parts = text.split("/", 1)
        bucket = parts[0]
        prefix = parts[1] if len(parts) > 1 else ""
        if bucket:
            self.navigate_to(bucket, prefix)

    def _navigate_up(self) -> None:
        if not self.current_prefix:
            return
        clean = self.current_prefix.rstrip("/")
        if "/" in clean:
            parent_prefix = clean.rsplit("/", 1)[0] + "/"
        else:
            parent_prefix = ""
        self.navigate_to(self.current_bucket, parent_prefix)

    def _refresh_current_view(self) -> None:
        if self.current_bucket:
            self.navigate_to(self.current_bucket, self.current_prefix)
        else:
            self._load_buckets_async()

    def _update_selection_info(self) -> None:
        selected = self.table_view.get_selected_rows()
        count = len(selected)
        if count == 0:
            self.lbl_selection_info.setText("0 items selected")
        else:
            total_size = sum(item.size for item in selected if not item.is_folder)
            self.lbl_selection_info.setText(f"{count} items selected ({format_bytes(total_size)})")

    def _download_selected_rows(self) -> None:
        selected = self.table_view.get_selected_rows()
        if not selected:
            QMessageBox.information(self, "No Selection", "Please select one or more files or folders to download.")
            return
        self._on_download_requested(selected)

    def _on_download_requested(self, rows: List[S3RowItem]) -> None:
        if not self.downloader:
            QMessageBox.warning(self, "Downloader", "Downloader is not ready.")
            return

        dest_dir = self.transfer_panel.download_dir

        def queue_work() -> None:
            for item in rows:
                if item.is_folder:
                    # Recursive folder download streaming
                    self.status_message.emit(f"Streaming folder s3://{self.current_bucket}/{item.key} into download queue...")
                    tree_iter = self.s3_client.iter_tree(self.current_bucket, item.key)
                    self.downloader.enqueue_tree(
                        bucket=self.current_bucket,
                        base_prefix=item.key,
                        file_iterator=tree_iter,
                        destination_dir=dest_dir / item.name,
                    )
                else:
                    self.downloader.enqueue_file(
                        bucket=self.current_bucket,
                        key=item.key,
                        size=item.size,
                        destination_dir=dest_dir,
                    )
            self.downloader.start()

        threading.Thread(target=queue_work, daemon=True).start()

    def _cancel_all_downloads(self) -> None:
        if self.downloader:
            self.downloader.cancel_all()
            self.status_message.emit("Cancelled all active and queued downloads.")

    def _on_download_dir_changed(self, new_dir: str) -> None:
        self.config_manager.settings.default_download_dir = new_dir
        self.config_manager.save_settings()
