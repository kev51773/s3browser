"""Qt Data Models for Tree, Virtual Table, and Transfer Queue."""

from __future__ import annotations

from datetime import datetime
from typing import Any, List, Optional

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QStandardItem, QStandardItemModel
import qtawesome as qta

from s3_browser.downloader import DownloadTask, TaskStatus
from s3_browser.s3_client import S3BucketItem, S3FileItem, S3FolderItem


def format_bytes(byte_count: int) -> str:
    """Formats bytes into human readable binary units."""
    if byte_count < 0:
        return "0 B"
    for unit in ["B", "KB", "MB", "GB", "TB", "PB"]:
        if byte_count < 1024.0:
            return f"{byte_count:.1f} {unit}" if unit != "B" else f"{byte_count} B"
        byte_count /= 1024.0
    return f"{byte_count:.1f} PB"


class S3FolderTreeModel(QStandardItemModel):
    """Hierarchical tree model for S3 buckets and folder prefixes."""

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self.setHorizontalHeaderLabels(["Buckets & Folders"])
        self._bucket_icon = qta.icon("fa5s.database", color="#e67e22")
        self._folder_icon = qta.icon("fa5s.folder", color="#3498db")

    def populate_buckets(self, buckets: List[S3BucketItem]) -> None:
        self.clear()
        self.setHorizontalHeaderLabels(["Buckets & Folders"])
        for b in buckets:
            item = QStandardItem(self._bucket_icon, b.name)
            item.setData(b.name, Qt.UserRole)  # Bucket name
            item.setData("", Qt.UserRole + 1)   # Prefix
            item.setData(True, Qt.UserRole + 2)  # Is bucket
            item.setData(False, Qt.UserRole + 3) # Is loaded
            # Add dummy child to show expandable chevron
            dummy = QStandardItem("Loading...")
            item.appendRow(dummy)
            self.appendRow(item)

    def set_subfolders(self, parent_item: QStandardItem, bucket: str, folders: List[S3FolderItem]) -> None:
        # Clear dummy/existing children
        parent_item.removeRows(0, parent_item.rowCount())
        parent_item.setData(True, Qt.UserRole + 3)  # Marked as loaded
        for f in folders:
            sub = QStandardItem(self._folder_icon, f.name)
            sub.setData(bucket, Qt.UserRole)
            sub.setData(f.prefix, Qt.UserRole + 1)
            sub.setData(False, Qt.UserRole + 2)  # Is not bucket
            sub.setData(False, Qt.UserRole + 3)  # Is loaded
            # Add dummy child so chevron appears if it has subfolders
            dummy = QStandardItem("Loading...")
            sub.appendRow(dummy)
            parent_item.appendRow(sub)


class S3RowItem:
    """Row wrapper representing either a folder or a file in the table."""

    def __init__(self, item: S3FolderItem | S3FileItem) -> None:
        self.raw = item
        self.is_folder = isinstance(item, S3FolderItem)
        if self.is_folder:
            self.name = item.name
            self.key = item.prefix
            self.size = -1
            self.modified: Optional[datetime] = None
            self.storage_class = "Folder"
        else:
            self.name = item.name
            self.key = item.key
            self.size = item.size
            self.modified = item.last_modified
            self.storage_class = item.storage_class


class S3TableModel(QAbstractTableModel):
    """High-performance virtual table model with sorting and instant filtering."""

    COLUMNS = ["Name", "Size", "Last Modified", "Storage Class"]

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self._all_rows: List[S3RowItem] = []
        self._filtered_rows: List[S3RowItem] = []
        self._filter_text = ""

        # Pre-cache icons for performance
        self._folder_icon = qta.icon("fa5s.folder", color="#3498db")
        self._file_icon = qta.icon("fa5s.file", color="#7f8c8d")
        self._code_icon = qta.icon("fa5s.file-code", color="#9b59b6")
        self._image_icon = qta.icon("fa5s.file-image", color="#2ecc71")
        self._archive_icon = qta.icon("fa5s.file-archive", color="#e74c3c")

    def _get_icon_for_name(self, name: str, is_folder: bool) -> QIcon:
        if is_folder:
            return self._folder_icon
        lower = name.lower()
        if lower.endswith((".zip", ".tar", ".gz", ".7z", ".rar")):
            return self._archive_icon
        if lower.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")):
            return self._image_icon
        if lower.endswith((".py", ".json", ".yaml", ".yml", ".xml", ".html", ".js", ".ts", ".csv", ".parquet")):
            return self._code_icon
        return self._file_icon

    def set_items(self, folders: List[S3FolderItem], files: List[S3FileItem]) -> None:
        self.beginResetModel()
        self._all_rows = [S3RowItem(f) for f in folders] + [S3RowItem(f) for f in files]
        self._apply_filter()
        self.endResetModel()

    def set_filter(self, text: str) -> None:
        self.beginResetModel()
        self._filter_text = text.strip().lower()
        self._apply_filter()
        self.endResetModel()

    def _apply_filter(self) -> None:
        if not self._filter_text:
            self._filtered_rows = list(self._all_rows)
        else:
            self._filtered_rows = [
                r for r in self._all_rows
                if self._filter_text in r.name.lower()
            ]

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self._filtered_rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self.COLUMNS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole) -> Any:
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.COLUMNS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> Any:
        if not index.isValid() or not (0 <= index.row() < len(self._filtered_rows)):
            return None

        row_item = self._filtered_rows[index.row()]
        col = index.column()

        if role == Qt.DisplayRole:
            if col == 0:
                return row_item.name
            elif col == 1:
                return "--" if row_item.is_folder else format_bytes(row_item.size)
            elif col == 2:
                if row_item.is_folder or not row_item.modified:
                    return "--"
                return row_item.modified.strftime("%Y-%m-%d %H:%M:%S")
            elif col == 3:
                return row_item.storage_class

        elif role == Qt.DecorationRole:
            if col == 0:
                return self._get_icon_for_name(row_item.name, row_item.is_folder)

        elif role == Qt.TextAlignmentRole:
            if col == 1:
                return int(Qt.AlignRight | Qt.AlignVCenter)
            return int(Qt.AlignLeft | Qt.AlignVCenter)

        elif role == Qt.UserRole:
            return row_item

        return None

    def sort(self, column: int, order: Qt.SortOrder = Qt.AscendingOrder) -> None:
        self.beginResetModel()
        reverse = (order == Qt.DescendingOrder)

        def sort_key(item: S3RowItem) -> Any:
            # Keep folders on top
            folder_weight = 0 if item.is_folder else 1
            if column == 0:
                val = item.name.lower()
            elif column == 1:
                val = item.size
            elif column == 2:
                val = item.modified.timestamp() if item.modified else 0
            elif column == 3:
                val = item.storage_class
            else:
                val = item.name.lower()
            return (folder_weight, val)

        self._filtered_rows.sort(key=sort_key, reverse=reverse)
        self.endResetModel()

    def get_row(self, row: int) -> Optional[S3RowItem]:
        if 0 <= row < len(self._filtered_rows):
            return self._filtered_rows[row]
        return None


class TransferQueueModel(QAbstractTableModel):
    """Virtual table model for the active and queued downloads."""

    COLUMNS = ["File", "Size", "Progress", "Speed", "ETA", "Status"]

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self.tasks: List[DownloadTask] = []
        self._task_index_map: dict[str, int] = {}

    def set_tasks(self, tasks: List[DownloadTask]) -> None:
        self.beginResetModel()
        self.tasks = tasks
        self._task_index_map = {t.task_id: idx for idx, t in enumerate(tasks)}
        self.endResetModel()

    def update_task(self, task: DownloadTask) -> None:
        idx = self._task_index_map.get(task.task_id)
        if idx is not None and 0 <= idx < len(self.tasks):
            self.tasks[idx] = task
            top_left = self.index(idx, 0)
            bottom_right = self.index(idx, len(self.COLUMNS) - 1)
            self.dataChanged.emit(top_left, bottom_right)
        else:
            # Append new task
            self.beginInsertRows(QModelIndex(), len(self.tasks), len(self.tasks))
            self._task_index_map[task.task_id] = len(self.tasks)
            self.tasks.append(task)
            self.endInsertRows()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self.tasks)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self.COLUMNS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole) -> Any:
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.COLUMNS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> Any:
        if not index.isValid() or not (0 <= index.row() < len(self.tasks)):
            return None

        task = self.tasks[index.row()]
        col = index.column()

        if role == Qt.DisplayRole:
            if col == 0:
                return task.filename
            elif col == 1:
                return format_bytes(task.total_bytes)
            elif col == 2:
                return f"{task.progress_pct:.1f}%"
            elif col == 3:
                return f"{format_bytes(int(task.speed_bps))}/s" if task.speed_bps > 0 else "--"
            elif col == 4:
                if task.status == TaskStatus.DOWNLOADING and task.eta_seconds > 0:
                    mins, secs = divmod(int(task.eta_seconds), 60)
                    return f"{mins}m {secs}s" if mins else f"{secs}s"
                return "--"
            elif col == 5:
                return task.status.value

        elif role == Qt.TextAlignmentRole:
            if col in (1, 2, 3, 4):
                return int(Qt.AlignRight | Qt.AlignVCenter)
            return int(Qt.AlignLeft | Qt.AlignVCenter)

        elif role == Qt.ForegroundRole:
            if col == 5:
                if task.status == TaskStatus.COMPLETED:
                    return QColor("#27ae60")
                elif task.status == TaskStatus.FAILED:
                    return QColor("#e74c3c")
                elif task.status == TaskStatus.DOWNLOADING:
                    return QColor("#2980b9")
                elif task.status == TaskStatus.SKIPPED:
                    return QColor("#7f8c8d")

        return None
