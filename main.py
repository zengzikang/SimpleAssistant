import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import Qt


def main():
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("SimpleAssistant")
    app.setApplicationDisplayName("简单助手")

    from src.utils.logger import setup_logger
    logger = setup_logger()
    logger.info("启动简单助手...")

    from src.config.manager import ConfigManager
    from src.db.database import Database
    from src.ui.tray_icon import TrayIcon

    config = ConfigManager()
    db = Database()

    tray = TrayIcon(config, db, app)
    tray.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
