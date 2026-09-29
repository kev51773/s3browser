"""Application entry point for S3 Browser."""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from s3_browser.config import ConfigManager
from s3_browser.ui.main_window import MainWindow


def handle_uncaught_exception(exc_type, exc_value, exc_traceback):
    """Logs unhandled exceptions gracefully without crashing silently."""
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    err_str = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
    print("Unhandled exception:\n", err_str, file=sys.stderr)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="S3 Browser")
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Enable offline demo mode with mock S3 data for testing without AWS credentials",
    )
    args, _ = parser.parse_known_args()

    # High-DPI support
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    sys.excepthook = handle_uncaught_exception

    app = QApplication(sys.argv)
    app.setApplicationName("S3 Browser")
    app.setOrganizationName("S3BrowserProject")

    # Clean cross-platform modern look (Fusion prevents macOS QStyle layout bugs)
    app.setStyle("Fusion")

    # Windows taskbar grouping & icon fix
    if sys.platform == "win32":
        import ctypes
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("s3browser.downloader.desktop.v1")
        except Exception:
            pass

    # Application icon (Windows .ico, macOS .icns or .png with optical small sizes)
    assets_dir = Path(__file__).resolve().parent.parent / "assets"
    icon_ico = assets_dir / "icon.ico"
    icon_icns = assets_dir / "icon.icns"

    app_icon = QIcon()
    from PySide6.QtCore import QSize
    for sz in [16, 24, 32, 48, 64, 128, 256]:
        p = assets_dir / f"icon_{sz}.png"
        if p.exists():
            app_icon.addFile(str(p), QSize(sz, sz))

    if sys.platform == "win32" and icon_ico.exists():
        app_icon.addFile(str(icon_ico))
    elif sys.platform == "darwin" and icon_icns.exists():
        app_icon.addFile(str(icon_icns))

    if not app_icon.isNull():
        app.setWindowIcon(app_icon)

    # Font setup
    font = app.font()
    if sys.platform == "darwin":
        font.setPointSize(12)
    else:
        font.setPointSize(10)
    app.setFont(font)

    config_manager = ConfigManager()
    window = MainWindow(config_manager=config_manager, allow_demo=args.demo)
    if not app_icon.isNull():
        window.setWindowIcon(app_icon)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
