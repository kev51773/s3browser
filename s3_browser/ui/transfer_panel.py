"""Bottom Transfer Manager panel matching S3 Browser layout."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QProgressBar,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)
import qtawesome as qta

from s3_browser.downloader import BulkDownloader, DownloadTask
from s3_browser.ui.models import TransferQueueModel, format_bytes


class TransferPanel(QFrame):
    """Bottom collapsible transfer manager with aggregate speed, progress, and queue."""

    destination_changed = Signal(str)
    cancel_requested = Signal()

    def __init__(self, default_download_dir: str, parent: Any = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.download_dir = Path(default_download_dir)
        self.download_dir.mkdir(parents=True, exist_ok=True)

        self.setAcceptDrops(True)  # Accept dragging local folders to set target

        self.model = TransferQueueModel(self)
        self._init_ui()

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(6, 6, 6, 6)
        main_layout.setSpacing(4)

        # Header bar: Destination + Aggregate stats + Controls
        header = QHBoxLayout()

        self.lbl_dest = QLabel(f"<b>Download to:</b> {self.download_dir}")
        self.lbl_dest.setToolTip("Drag and drop any local folder here to change destination.")

        self.btn_change_dest = QPushButton(qta.icon("fa5s.folder-open"), "Change...")
        self.btn_change_dest.clicked.connect(self._choose_destination)

        self.btn_open_folder = QPushButton(qta.icon("fa5s.external-link-alt"), "Open in Finder/Explorer")
        self.btn_open_folder.clicked.connect(self._open_destination_in_system)

        header.addWidget(self.lbl_dest)
        header.addWidget(self.btn_change_dest)
        header.addWidget(self.btn_open_folder)
        header.addStretch()

        # Global aggregate progress bar
        self.global_progress = QProgressBar()
        self.global_progress.setRange(0, 100)
        self.global_progress.setValue(0)
        self.global_progress.setTextVisible(True)
        self.global_progress.setFixedWidth(200)

        # Speed and ETA label
        self.lbl_speed_eta = QLabel("Idle")
        self.lbl_speed_eta.setStyleSheet("font-weight: bold; color: #2c3e50;")

        self.btn_cancel_all = QPushButton(qta.icon("fa5s.times-circle", color="#e74c3c"), "Cancel All")
        self.btn_cancel_all.clicked.connect(self.cancel_requested.emit)

        header.addWidget(self.global_progress)
        header.addWidget(self.lbl_speed_eta)
        header.addWidget(self.btn_cancel_all)

        main_layout.addLayout(header)

        # Transfers Table
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(24)

        th = self.table.horizontalHeader()
        th.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        th.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        th.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        th.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        th.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        th.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)

        main_layout.addWidget(self.table)

    def set_download_dir(self, path: Path) -> None:
        self.download_dir = path
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.lbl_dest.setText(f"<b>Download to:</b> {self.download_dir}")
        self.destination_changed.emit(str(self.download_dir))

    def _choose_destination(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Select Download Destination Folder", str(self.download_dir)
        )
        if folder:
            self.set_download_dir(Path(folder))

    def _open_destination_in_system(self) -> None:
        p = str(self.download_dir)
        if sys.platform == "darwin":
            subprocess.run(["open", p])
        elif sys.platform == "win32":
            os.startfile(p)
        else:
            subprocess.run(["xdg-open", p])

    def update_task(self, task: DownloadTask) -> None:
        self.model.update_task(task)

    def update_summary(self, summary: Dict[str, Any]) -> None:
        pct = int(summary.get("progress_pct", 0))
        self.global_progress.setValue(pct)

        speed_bps = summary.get("speed_bps", 0.0)
        eta_sec = summary.get("eta_seconds", 0.0)
        comp = summary.get("completed_files", 0)
        total = summary.get("total_files", 0)
        dl_b = summary.get("downloaded_bytes", 0)
        tot_b = summary.get("total_bytes", 0)

        if total > 0 and comp < total:
            speed_str = f"{format_bytes(int(speed_bps))}/s"
            if eta_sec > 0:
                mins, secs = divmod(int(eta_sec), 60)
                eta_str = f"{mins}m {secs}s" if mins else f"{secs}s"
            else:
                eta_str = "--"
            self.lbl_speed_eta.setText(
                f"{comp}/{total} files | {format_bytes(dl_b)} / {format_bytes(tot_b)} | Speed: {speed_str} | ETA: {eta_str}"
            )
        elif total > 0 and comp == total:
            self.lbl_speed_eta.setText(f"All {total} files completed ({format_bytes(tot_b)}).")
        else:
            self.lbl_speed_eta.setText("Idle")

    # Drag and Drop support: User drops a local folder to set destination
    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        for url in event.mimeData().urls():
            local_path = Path(url.toLocalFile())
            if local_path.is_dir():
                self.set_download_dir(local_path)
                event.acceptProposedAction()
                return
        super().dropEvent(event)
