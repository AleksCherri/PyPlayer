import sys
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui  import QGuiApplication
from PySide6.QtQml  import QQmlApplicationEngine

BASE_DIR  = Path(__file__).resolve().parent
DATA_FILE = BASE_DIR / 'data' / 'music_data.json'
QML_DIR   = BASE_DIR / 'qml'


def main():
    app = QGuiApplication(sys.argv)
    engine = QQmlApplicationEngine()

    engine.load(QUrl.fromLocalFile(str(QML_DIR / 'Main.qml')))

    if not engine.rootObjects():
        print('Failed to load QML')
        sys.exit(-1)

    sys.exit(app.exec())


if __name__ == '__main__':
    main()
