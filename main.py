"""Точка входа PyPlayer: QGuiApplication + модели + QML-интерфейс."""
import sys
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

from models.playlist_item_model import PlaylistItemModel
from models.playlist_model import PlaylistModel
from models.settings_model import SettingsModel
from models.track_filter_model import TrackFilterModel
from models.track_model import TrackModel
from services.audio.engine import PlaybackEngine
from services.config import load_config
from services.controller import PlaybackController
from services.db import Database
from services.library import resolve_music_dir, scan_library

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / 'data'
QML_DIR = BASE_DIR / 'qml'


def main() -> None:
    """Запуск: конфиг, БД, модели, контроллер и трёхколоночный QML."""
    config_path = DATA_DIR / 'config.json'
    config = load_config(config_path)

    db = Database(DATA_DIR / 'pyplayer.db')
    music_dir = resolve_music_dir(config['music_dir'], BASE_DIR)
    if music_dir is not None:
        music_dir.mkdir(parents=True, exist_ok=True)
    scan_library(db, music_dir)

    app = QGuiApplication(sys.argv)
    engine = QQmlApplicationEngine()

    # Модели и контроллер живут до выхода из app.exec().
    track_model = TrackModel(db)
    track_model.reload()
    track_filter = TrackFilterModel()
    track_filter.setSourceModel(track_model)

    playlist_model = PlaylistModel(db)
    playlist_model.reload()
    playlist_items = PlaylistItemModel(db)

    playback = PlaybackController(
        db, PlaybackEngine(),
        max_depth=int(config['max_playlist_depth']))
    settings = SettingsModel(config_path, config)

    context = engine.rootContext()
    context.setContextProperty('trackModel', track_model)
    context.setContextProperty('trackFilter', track_filter)
    context.setContextProperty('playlistModel', playlist_model)
    context.setContextProperty('playlistItems', playlist_items)
    context.setContextProperty('player', playback)
    context.setContextProperty('settings', settings)
    context.setContextProperty('musicDir', str(music_dir or ''))

    app.aboutToQuit.connect(playback.shutdown)

    engine.load(QUrl.fromLocalFile(str(QML_DIR / 'Main.qml')))
    if not engine.rootObjects():
        print('Не удалось загрузить QML', file=sys.stderr)
        sys.exit(-1)

    sys.exit(app.exec())


if __name__ == '__main__':
    main()
