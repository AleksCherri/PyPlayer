"""QAbstractListModel с треками для колонки «Музыкальные файлы»."""
import sqlite3
from typing import Any

from PySide6.QtCore import QAbstractListModel, QByteArray, QModelIndex, Qt

from services.db import Database
from services.library import fetch_tracks


class TrackModel(QAbstractListModel):
    """Список треков из БД; обновляется через ``reload()``."""

    ID_ROLE = Qt.ItemDataRole.UserRole + 1
    PATH_ROLE = Qt.ItemDataRole.UserRole + 2
    TITLE_ROLE = Qt.ItemDataRole.UserRole + 3
    ARTIST_ROLE = Qt.ItemDataRole.UserRole + 4
    DURATION_ROLE = Qt.ItemDataRole.UserRole + 5
    SAMPLE_RATE_ROLE = Qt.ItemDataRole.UserRole + 6
    FORMAT_ROLE = Qt.ItemDataRole.UserRole + 7

    def __init__(self, db: Database, parent: Any = None) -> None:
        super().__init__(parent)
        self._db = db
        self._tracks: list[sqlite3.Row] = []

    def reload(self) -> None:
        """Перечитывает треки из БД (сброс и заполнение модели заново)."""
        self.beginResetModel()
        self._tracks = fetch_tracks(self._db)
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._tracks)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not 0 <= index.row() < len(self._tracks):
            return None
        row = self._tracks[index.row()]
        field = _ROLE_FIELDS.get(role)
        return row[field] if field is not None else None

    def roleNames(self) -> dict[int, QByteArray]:
        return {
            self.ID_ROLE: QByteArray(b'id'),
            self.PATH_ROLE: QByteArray(b'path'),
            self.TITLE_ROLE: QByteArray(b'title'),
            self.ARTIST_ROLE: QByteArray(b'artist'),
            self.DURATION_ROLE: QByteArray(b'duration'),
            self.SAMPLE_RATE_ROLE: QByteArray(b'sampleRate'),
            self.FORMAT_ROLE: QByteArray(b'format'),
        }


_ROLE_FIELDS: dict[int, str] = {
    Qt.ItemDataRole.DisplayRole: 'title',
    TrackModel.ID_ROLE: 'id',
    TrackModel.PATH_ROLE: 'path',
    TrackModel.TITLE_ROLE: 'title',
    TrackModel.ARTIST_ROLE: 'artist',
    TrackModel.DURATION_ROLE: 'duration_sec',
    TrackModel.SAMPLE_RATE_ROLE: 'sample_rate',
    TrackModel.FORMAT_ROLE: 'format',
}
