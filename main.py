from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv
from PySide6.QtWidgets import QApplication

from gui.main_window import MainWindow


def main() -> int:
    project_root = Path(__file__).resolve().parent
    load_dotenv(project_root / ".env")

    app = QApplication(sys.argv)
    window = MainWindow(project_root=project_root)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
