"""Dialogs for adding and organizing bookmarks into cascading categories."""

from __future__ import annotations

from typing import Any, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)
import qtawesome as qta

from s3_browser.config import BookmarkFolder, BookmarkItem, ConfigManager


class BookmarkTreeWidget(QTreeWidget):
    """TreeWidget supporting internal drag-and-drop re-parenting."""

    item_dropped = Signal()

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)

    def dropEvent(self, event: Any) -> None:
        super().dropEvent(event)
        self.item_dropped.emit()


class AddBookmarkDialog(QDialog):
    """Dialog to create a new bookmark with category folder and current path/profile."""

    def __init__(
        self,
        config_manager: ConfigManager,
        current_profile: str,
        current_path: str,
        parent: Any = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add S3 Bookmark")
        self.setMinimumSize(520, 260)
        self.resize(560, 280)
        self.config_manager = config_manager

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(10)

        # Extract a friendly default name from path
        default_name = current_path.rstrip("/").split("/")[-1] or "Root"

        self.edit_name = QLineEdit(default_name)
        self.edit_path = QLineEdit(current_path)

        # Profile combo: lists all configured profiles + Demo with current pre-selected
        self.combo_profile = QComboBox()
        self.combo_profile.setEditable(True)
        available_profiles = list(self.config_manager.profiles.keys())
        if "Demo" not in available_profiles:
            available_profiles.append("Demo")
        for p in available_profiles:
            self.combo_profile.addItem(p)

        # Preselect current profile
        idx = self.combo_profile.findText(current_profile)
        if idx >= 0:
            self.combo_profile.setCurrentIndex(idx)
        else:
            self.combo_profile.setCurrentText(current_profile or "Default")

        # Folder combo
        self.combo_folder = QComboBox()
        self.combo_folder.setEditable(True)
        self.combo_folder.addItem("(No Folder / Root Menu)")

        # Collect existing folders
        for b in self.config_manager.bookmarks:
            if isinstance(b, BookmarkFolder):
                self.combo_folder.addItem(b.name)

        for w in (self.edit_name, self.edit_path, self.combo_profile, self.combo_folder):
            w.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            w.setMinimumHeight(28)

        form.addRow("Bookmark Name:", self.edit_name)
        form.addRow("S3 Path:", self.edit_path)
        form.addRow("AWS Profile:", self.combo_profile)
        form.addRow("Folder / Category:", self.combo_folder)

        layout.addLayout(form)

        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btn_box.accepted.connect(self._on_accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    def _on_accept(self) -> None:
        name = self.edit_name.text().strip()
        path = self.edit_path.text().strip()
        profile = self.combo_profile.currentText().strip()
        folder_text = self.combo_folder.currentText().strip()

        if not name or not path:
            QMessageBox.warning(self, "Invalid Bookmark", "Please enter both a name and an S3 path.")
            return

        folder = folder_text if folder_text and folder_text != "(No Folder / Root Menu)" else None
        item = BookmarkItem(name=name, profile=profile, path=path)
        self.config_manager.add_bookmark(item, folder_name=folder)
        self.accept()


class OrganizeBookmarksDialog(QDialog):
    """Dialog to manage, rename, re-parent, or delete bookmarks and folders."""

    bookmarks_changed = Signal()

    def __init__(self, config_manager: ConfigManager, parent: Any = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Organize Bookmarks")
        self.setMinimumSize(680, 460)
        self.resize(760, 500)
        self.config_manager = config_manager

        self._folder_icon = qta.icon("fa5s.folder", color="#3498db")
        self._bookmark_icon = qta.icon("fa5s.bookmark", color="#f39c12")

        self._init_ui()
        self._populate_tree()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)

        self.tree = BookmarkTreeWidget()
        self.tree.setHeaderLabels(["Bookmark / Folder", "Profile", "S3 Path"])
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.tree.header().resizeSection(0, 260)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_context_menu)
        self.tree.item_dropped.connect(self._rebuild_from_tree)
        layout.addWidget(self.tree)

        btn_layout = QHBoxLayout()
        self.btn_new_folder = QPushButton(qta.icon("fa5s.folder-plus"), "New Folder")
        self.btn_new_folder.clicked.connect(self._create_folder)
        self.btn_move = QPushButton(qta.icon("fa5s.arrows-alt"), "Move To...")
        self.btn_move.clicked.connect(self._move_item)
        self.btn_rename = QPushButton(qta.icon("fa5s.edit"), "Rename")
        self.btn_rename.clicked.connect(self._rename_item)
        self.btn_delete = QPushButton(qta.icon("fa5s.trash"), "Delete")
        self.btn_delete.clicked.connect(self._delete_item)

        btn_layout.addWidget(self.btn_new_folder)
        btn_layout.addWidget(self.btn_move)
        btn_layout.addWidget(self.btn_rename)
        btn_layout.addWidget(self.btn_delete)
        btn_layout.addStretch()

        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)
        btn_layout.addWidget(btn_close)

        layout.addLayout(btn_layout)

    def _populate_tree(self) -> None:
        self.tree.clear()

        def add_items(parent_tree_item: Optional[QTreeWidgetItem], items: List[BookmarkFolder | BookmarkItem]) -> None:
            for b in items:
                if isinstance(b, BookmarkFolder):
                    f_item = QTreeWidgetItem([b.name, "", ""])
                    f_item.setIcon(0, self._folder_icon)
                    f_item.setData(0, Qt.UserRole, b)
                    f_item.setFlags(
                        Qt.ItemFlag.ItemIsEnabled
                        | Qt.ItemFlag.ItemIsSelectable
                        | Qt.ItemFlag.ItemIsDragEnabled
                        | Qt.ItemFlag.ItemIsDropEnabled
                    )
                    add_items(f_item, b.children)
                    if parent_tree_item:
                        parent_tree_item.addChild(f_item)
                    else:
                        self.tree.addTopLevelItem(f_item)
                else:
                    item = QTreeWidgetItem([b.name, b.profile, b.path])
                    item.setIcon(0, self._bookmark_icon)
                    item.setData(0, Qt.UserRole, b)
                    item.setFlags(
                        Qt.ItemFlag.ItemIsEnabled
                        | Qt.ItemFlag.ItemIsSelectable
                        | Qt.ItemFlag.ItemIsDragEnabled
                    )
                    if parent_tree_item:
                        parent_tree_item.addChild(item)
                    else:
                        self.tree.addTopLevelItem(item)

        add_items(None, self.config_manager.bookmarks)
        self.tree.expandAll()

    def _show_context_menu(self, pos: Any) -> None:
        item = self.tree.itemAt(pos)
        if not item:
            return
        self.tree.setCurrentItem(item)
        menu = QMenu(self)
        action_move = menu.addAction(qta.icon("fa5s.arrows-alt"), "Move To...")
        action_move.triggered.connect(self._move_item)
        action_rename = menu.addAction(qta.icon("fa5s.edit"), "Rename")
        action_rename.triggered.connect(self._rename_item)
        action_delete = menu.addAction(qta.icon("fa5s.trash"), "Delete")
        action_delete.triggered.connect(self._delete_item)
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _create_folder(self) -> None:
        name, ok = QInputDialog.getText(self, "New Folder", "Enter folder name:")
        if ok and name.strip():
            folder = BookmarkFolder(name=name.strip())
            self.config_manager.bookmarks.append(folder)
            self.config_manager.save_bookmarks()
            self._populate_tree()
            self._select_data_item(folder)
            self.bookmarks_changed.emit()

    def _move_item(self) -> None:
        current = self.tree.currentItem()
        if not current:
            QMessageBox.information(self, "Move Item", "Please select a bookmark or folder to move.")
            return

        data = current.data(0, Qt.UserRole)
        if not data:
            return

        # Recursively collect available destination folders
        def collect_folders(container: List[BookmarkFolder | BookmarkItem], path: str = "") -> List[tuple[str, BookmarkFolder]]:
            res: List[tuple[str, BookmarkFolder]] = []
            for b in container:
                if isinstance(b, BookmarkFolder):
                    # Prevent moving folder into itself or descendant
                    if isinstance(data, BookmarkFolder) and (b is data or self.config_manager.is_descendant(data, b)):
                        continue
                    folder_path = f"{path}/{b.name}" if path else b.name
                    res.append((folder_path, b))
                    res.extend(collect_folders(b.children, folder_path))
            return res

        folder_options = collect_folders(self.config_manager.bookmarks)
        choices = ["(Root / Top Level)"] + [f[0] for f in folder_options]

        dest_name, ok = QInputDialog.getItem(
            self,
            "Move Bookmark",
            f"Move '{data.name}' to:",
            choices,
            current=0,
            editable=False,
        )
        if not ok or not dest_name:
            return

        target_folder = None
        if dest_name != "(Root / Top Level)":
            for path, f in folder_options:
                if path == dest_name:
                    target_folder = f
                    break

        if self.config_manager.move_bookmark(data, target_folder):
            self._populate_tree()
            self._select_data_item(data)
            self.bookmarks_changed.emit()

    def _select_data_item(self, target_data: Any) -> None:
        def search(item: QTreeWidgetItem) -> bool:
            if item.data(0, Qt.UserRole) is target_data:
                self.tree.setCurrentItem(item)
                item.setSelected(True)
                return True
            for i in range(item.childCount()):
                if search(item.child(i)):
                    return True
            return False

        for i in range(self.tree.topLevelItemCount()):
            if search(self.tree.topLevelItem(i)):
                break

    def _rebuild_from_tree(self) -> None:
        def parse_item(item: QTreeWidgetItem) -> Optional[BookmarkFolder | BookmarkItem]:
            data = item.data(0, Qt.UserRole)
            if isinstance(data, BookmarkFolder):
                data.children = []
                for i in range(item.childCount()):
                    child = parse_item(item.child(i))
                    if child is not None:
                        data.children.append(child)
                return data
            elif isinstance(data, BookmarkItem):
                return data
            return None

        new_bookmarks: List[BookmarkFolder | BookmarkItem] = []
        for i in range(self.tree.topLevelItemCount()):
            parsed = parse_item(self.tree.topLevelItem(i))
            if parsed is not None:
                new_bookmarks.append(parsed)

        self.config_manager.bookmarks = new_bookmarks
        self.config_manager.save_bookmarks()
        self.bookmarks_changed.emit()

    def _rename_item(self) -> None:
        current = self.tree.currentItem()
        if not current:
            return
        data = current.data(0, Qt.UserRole)
        new_name, ok = QInputDialog.getText(self, "Rename", "Enter new name:", text=current.text(0))
        if ok and new_name.strip():
            data.name = new_name.strip()
            self.config_manager.save_bookmarks()
            self._populate_tree()
            self._select_data_item(data)
            self.bookmarks_changed.emit()

    def _delete_item(self) -> None:
        current = self.tree.currentItem()
        if not current:
            return
        data = current.data(0, Qt.UserRole)
        ans = QMessageBox.question(self, "Confirm Delete", f"Delete '{data.name}'?")
        if ans != QMessageBox.StandardButton.Yes:
            return

        self.config_manager.remove_bookmark(data)
        self._populate_tree()
        self.bookmarks_changed.emit()

