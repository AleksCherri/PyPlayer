"""Точка входа PyPlayer: создаёт QGuiApplication и загружает QML-интерфейс."""
import sys
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

from services.config import load_config

BASE_DIR = Path(__file__).resolve().parent
QML_DIR = BASE_DIR / 'qml'


def main() -> None:
    """Запуск приложения: конфиг + QML-движок с трёхколоночным окном."""
    load_config(BASE_DIR / 'data' / 'config.json')

    app = QGuiApplication(sys.argv)
    engine = QQmlApplicationEngine()

    engine.load(QUrl.fromLocalFile(str(QML_DIR / 'Main.qml')))

    if not engine.rootObjects():
        print('Не удалось загрузить QML', file=sys.stderr)
        sys.exit(-1)

    sys.exit(app.exec())


if __name__ == '__main__':
    main()
