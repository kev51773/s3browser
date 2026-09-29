"""Custom views for S3 objects with native macOS interaction."""

from __future__ import annotations

from typing import Any, List, Optional

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QAction, QDrag, QKeySequence, QMouseEvent, QPainter, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QHeaderView,
    QMenu,
    QRubberBand,
    QTableView,
)
import qtawesome as qta

from s3_browser.ui.models import S3RowItem, S3TableModel


class S3TableView(QTableView):
    """S3 Object table view with rubberband drag selection, macOS hotkeys, and context menu."""

    folder_double_clicked = Signal(str)  # Emits prefix
    download_requested = Signal(list)    # Emits list of S3RowItem
    navigate_up_requested = Signal()

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setSortingEnabled(True)
        self.setShowGrid(False)
        self.setAlternatingRowColors(True)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(28)

        # Header sizing
        self.apply_header_sizing()

        # Rubberband selection support
        self._rubber_band: Optional[QRubberBand] = None
        self._rubber_origin = QPoint()

        # Connect signals
        self.doubleClicked.connect(self._on_double_clicked)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

    def setModel(self, model: Any) -> None:
        super().setModel(model)
        self.apply_header_sizing()

    def apply_header_sizing(self) -> None:
        header = self.horizontalHeader()
        header.setMinimumSectionSize(60)
        if header.count() >= 4:
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
            header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
            header.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
            header.resizeSection(1, 100)
            header.resizeSection(2, 160)
            header.resizeSection(3, 130)

    def get_selected_rows(self) -> List[S3RowItem]:
        """Returns list of selected S3RowItem objects."""
        model: Optional[S3TableModel] = self.model()  # type: ignore
        if not model:
            return []
        selected_indexes = self.selectionModel().selectedRows(0)
        items: List[S3RowItem] = []
        for idx in selected_indexes:
            row_item = model.get_row(idx.row())
            if row_item:
                items.append(row_item)
        return items

    def _on_double_clicked(self, index: Any) -> None:
        model: Optional[S3TableModel] = self.model()  # type: ignore
        if not model or not index.isValid():
            return
        row_item = model.get_row(index.row())
        if not row_item:
            return

        if row_item.is_folder:
            self.folder_double_clicked.emit(row_item.key)
        else:
            self.download_requested.emit([row_item])

    def keyPressEvent(self, event: Any) -> None:
        key = event.key()
        modifiers = event.modifiers()
        is_ctrl_or_cmd = bool(modifiers & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier))

        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            selected = self.get_selected_rows()
            if len(selected) == 1 and selected[0].is_folder:
                self.folder_double_clicked.emit(selected[0].key)
                return
            elif selected:
                self.download_requested.emit(selected)
                return

        elif key == Qt.Key.Key_Backspace or (is_ctrl_or_cmd and key == Qt.Key.Key_Up):
            self.navigate_up_requested.emit()
            return

        elif is_ctrl_or_cmd and key == Qt.Key.Key_D:
            selected = self.get_selected_rows()
            if selected:
                self.download_requested.emit(selected)
                return

        super().keyPressEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        # Check if clicked in blank area below rows
        idx = self.indexAt(event.pos())
        if not idx.isValid() and event.button() == Qt.MouseButton.LeftButton:
            # Start rubberband box selection
            self._rubber_origin = event.pos()
            if not self._rubber_band:
                self._rubber_band = QRubberBand(QRubberBand.Shape.Rectangle, self)
            self._rubber_band.setGeometry(self._rubber_origin.x(), self._rubber_origin.y(), 0, 0)
            self._rubber_band.show()
            if not (event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier | Qt.KeyboardModifier.ShiftModifier)):
                self.clearSelection()
            return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._rubber_band and self._rubber_band.isVisible():
            rect = self._rubber_origin
            current = event.pos()
            top_left = QPoint(min(rect.x(), current.x()), min(rect.y(), current.y()))
            bottom_right = QPoint(max(rect.x(), current.x()), max(rect.y(), current.y()))
            self._rubber_band.setGeometry(top_left.x(), top_left.y(), bottom_right.x() - top_left.x(), bottom_right.y() - top_left.y())

            # Select items within rect
            model = self.model()
            if model:
                top_idx = self.indexAt(top_left)
                bot_idx = self.indexAt(bottom_right)
                start_r = top_idx.row() if top_idx.isValid() else 0
                end_r = bot_idx.row() if bot_idx.isValid() else model.rowCount() - 1
                if start_r >= 0 and end_r >= 0:
                    for r in range(min(start_r, end_r), max(start_r, end_r) + 1):
                        self.selectRow(r)
            return

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._rubber_band and self._rubber_band.isVisible():
            self._rubber_band.hide()
        super().mouseReleaseEvent(event)

    def _show_context_menu(self, pos: QPoint) -> None:
        selected = self.get_selected_rows()
        if not selected:
            return

        menu = QMenu(self)

        dl_action = QAction(qta.icon("fa5s.download", color="#27ae60"), f"Download Selected ({len(selected)} items)...", self)
        dl_action.setShortcut(QKeySequence("Ctrl+D"))
        dl_action.triggered.connect(lambda: self.download_requested.emit(selected))
        menu.addAction(dl_action)

        menu.addSeparator()

        copy_uri_action = QAction(qta.icon("fa5s.link"), "Copy S3 URI(s)", self)
        copy_uri_action.triggered.connect(lambda: self._copy_uris(selected))
        menu.addAction(copy_uri_action)

        copy_key_action = QAction(qta.icon("fa5s.copy"), "Copy Object Key(s)", self)
        copy_key_action.triggered.connect(lambda: self._copy_keys(selected))
        menu.addAction(copy_key_action)

        menu.exec(self.viewport().mapToGlobal(pos))

    def _copy_uris(self, items: List[S3RowItem]) -> None:
        clipboard = QApplication.clipboard()
        uris = [f"s3://{item.key}" for item in items]
        clipboard.setText("\n".join(uris))

    def _copy_keys(self, items: List[S3RowItem]) -> None:
        clipboard = QApplication.clipboard()
        keys = [item.key for item in items]
        clipboard.setText("\n".join(keys))
