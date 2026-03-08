from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from gui.main_window import MainWindow


def resolve_project_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def app_icon_path(project_root: Path) -> Path | None:
    for relative_path in ("images/app_icon.png", "images/app_icon.ico"):
        candidate = project_root / relative_path
        if candidate.exists():
            return candidate
    return None


def configure_windows_app_id() -> None:
    if sys.platform != "win32":
        return
    try:
        from ctypes import windll

        windll.shell32.SetCurrentProcessExplicitAppUserModelID("JamieK32.AnkiGen")
    except Exception:
        pass


def main() -> int:
    project_root = resolve_project_root()
    load_dotenv(project_root / ".env")
    configure_windows_app_id()

    app = QApplication(sys.argv)
    app.setApplicationName("AnkiGen")
    icon_path = app_icon_path(project_root)
    if icon_path is not None:
        icon = QIcon(str(icon_path))
        app.setWindowIcon(icon)
    else:
        icon = QIcon()
    window = MainWindow(project_root=project_root)
    if not icon.isNull():
        window.setWindowIcon(icon)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
