"""Модель плейлистов (колонка 1) с операциями над БД для QML."""
from __future__ import annotations

import sqlite3
from typing import Any

from PySide6.QtCore import (
    QAbstractListModel, QByteArray, QModelIndex, Qt, Signal, Slot,
)

from services.db import Database
from services import playlists as playlist_db


class PlaylistModel(QAbstractListModel):
    """Список плейлистов; все изменения сохраняются в БД."""

    ID_ROLE = Qt.ItemDataRole.UserRole + 1
    NAME_ROLE = Qt.ItemDataRole.UserRole + 2
    DESCRIPTION_ROLE = Qt.ItemDataRole.UserRole + 3
    CREATED_ROLE = Qt.ItemDataRole.UserRole + 4
    COUNT_ROLE = Qt.ItemDataRole.UserRole + 5
    MODE_ROLE = Qt.ItemDataRole.UserRole + 6

    playlistsChanged = Signal()
    errorOccurred = Signal(str)

    def __init__(self, db: Database, parent: Any = None) -> None:
        super().__init__(parent)
        self._db = db
        self._rows: list[dict[str, Any]] = []

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index: QModelIndex,
             role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        row = self._rows[index.row()]
        field = _ROLE_FIELDS.get(role)
        return row[field] if field is not None else None

    def roleNames(self) -> dict[int, QByteArray]:
        return {
            self.ID_ROLE: QByteArray(b'playlistId'),
            self.NAME_ROLE: QByteArray(b'name'),
            self.DESCRIPTION_ROLE: QByteArray(b'description'),
            self.CREATED_ROLE: QByteArray(b'createdAt'),
            self.COUNT_ROLE: QByteArray(b'trackCount'),
            self.MODE_ROLE: QByteArray(b'orderMode'),
        }

    @Slot()
    def reload(self) -> None:
        """Перечитать плейлисты из БД."""
        self.beginResetModel()
        try:
            self._rows = playlist_db.playlists_info(self._db)
        finally:
            self.endResetModel()
        self.playlistsChanged.emit()

    def _mutate(self, action) -> None:
        try:
            action()
        except (playlist_db.PlaylistError, sqlite3.Error) as exc:
            self.errorOccurred.emit(str(exc))
            return
        self.reload()

    @Slot(str, str)
    def create(self, name: str, description: str = '') -> None:
        self._mutate(lambda: playlist_db.create_playlist(
            self._db, name, description))

    @Slot(int, str)
    def rename(self, playlist_id: int, name: str) -> None:
        self._mutate(lambda: playlist_db.rename_playlist(
            self._db, playlist_id, name))

    @Slot(int, str)
    def setDescription(self, playlist_id: int, description: str) -> None:
        self._mutate(lambda: playlist_db.set_description(
            self._db, playlist_id, description))

    @Slot(int)
    def remove(self, playlist_id: int) -> None:
        self._mutate(lambda: playlist_db.delete_playlist(
            self._db, playlist_id))

    @Slot(int, str)
    def setOrderMode(self, playlist_id: int, mode: str) -> None:
        self._mutate(lambda: playlist_db.set_order_mode(
            self._db, playlist_id, mode))

    @Slot(int, result='QVariantMap')
    def stats(self, playlist_id: int) -> dict[str, float]:
        """Статистика плейлиста для панели информации (todo.txt, п. 30)."""
        try:
            return playlist_db.playlist_stats(self._db, playlist_id)
        except sqlite3.Error as exc:
            self.errorOccurred.emit(str(exc))
            return {}


_ROLE_FIELDS: dict[int, str] = {
    Qt.ItemDataRole.DisplayRole: 'name',
    PlaylistModel.ID_ROLE: 'id',
    PlaylistModel.NAME_ROLE: 'name',
    PlaylistModel.DESCRIPTION_ROLE: 'description',
    PlaylistModel.CREATED_ROLE: 'created_at',
    PlaylistModel.COUNT_ROLE: 'track_count',
    PlaylistModel.MODE_ROLE: 'order_mode',
}
