"""Модель содержимого выбранного плейлиста (колонка 1, дерево)."""
from __future__ import annotations

import sqlite3
from typing import Any

from PySide6.QtCore import (
    QAbstractListModel, QByteArray, QModelIndex, Qt, Signal, Slot,
)

from services.db import Database
from services import playlists as playlist_db


class PlaylistItemModel(QAbstractListModel):
    """Элементы плейлиста; поддерживает drop треков и перетаскивание внутри."""

    ID_ROLE = Qt.ItemDataRole.UserRole + 1
    POSITION_ROLE = Qt.ItemDataRole.UserRole + 2
    REF_TYPE_ROLE = Qt.ItemDataRole.UserRole + 3
    REF_ID_ROLE = Qt.ItemDataRole.UserRole + 4
    TITLE_ROLE = Qt.ItemDataRole.UserRole + 5
    ARTIST_ROLE = Qt.ItemDataRole.UserRole + 6
    DURATION_ROLE = Qt.ItemDataRole.UserRole + 7

    itemsChanged = Signal()
    errorOccurred = Signal(str)

    def __init__(self, db: Database, parent: Any = None) -> None:
        super().__init__(parent)
        self._db = db
        self._playlist_id: int | None = None
        self._rows: list[dict[str, Any]] = []

    @property
    def playlist_id(self) -> int | None:
        return self._playlist_id

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
            self.ID_ROLE: QByteArray(b'itemId'),
            self.POSITION_ROLE: QByteArray(b'position'),
            self.REF_TYPE_ROLE: QByteArray(b'refType'),
            self.REF_ID_ROLE: QByteArray(b'refId'),
            self.TITLE_ROLE: QByteArray(b'title'),
            self.ARTIST_ROLE: QByteArray(b'artist'),
            self.DURATION_ROLE: QByteArray(b'duration'),
        }

    @Slot(int)
    def setPlaylist(self, playlist_id: int) -> None:
        """Выбрать плейлист (вызывается при клике в колонке 1)."""
        self._playlist_id = playlist_id
        self.reload()

    @Slot()
    def reload(self) -> None:
        self.beginResetModel()
        try:
            self._rows = (playlist_db.items_info(self._db, self._playlist_id)
                          if self._playlist_id is not None else [])
        finally:
            self.endResetModel()
        self.itemsChanged.emit()

    def _mutate(self, action) -> None:
        if self._playlist_id is None:
            return
        try:
            action()
        except (playlist_db.PlaylistError, sqlite3.Error) as exc:
            self.errorOccurred.emit(str(exc))
            return
        self.reload()

    @Slot(int)
    def addTrack(self, track_id: int) -> None:
        """Принять трек, перетащенный из колонки 2."""
        playlist_id = self._playlist_id
        self._mutate(lambda: playlist_db.add_item(
            self._db, playlist_id, 'track', track_id))

    @Slot(int)
    def addPlaylist(self, ref_id: int) -> None:
        """Добавить вложенный плейлист."""
        playlist_id = self._playlist_id
        self._mutate(lambda: playlist_db.add_item(
            self._db, playlist_id, 'playlist', ref_id))

    @Slot(int)
    def removeAt(self, position: int) -> None:
        playlist_id = self._playlist_id
        self._mutate(lambda: playlist_db.remove_item(
            self._db, playlist_id, position))

    @Slot(int, int)
    def moveItem(self, from_position: int, to_position: int) -> None:
        """Перестановка элемента внутри плейлиста (drop на другой элемент)."""
        playlist_id = self._playlist_id
        self._mutate(lambda: playlist_db.move_item(
            self._db, playlist_id, from_position, to_position))


_ROLE_FIELDS: dict[int, str] = {
    Qt.ItemDataRole.DisplayRole: 'title',
    PlaylistItemModel.ID_ROLE: 'id',
    PlaylistItemModel.POSITION_ROLE: 'position',
    PlaylistItemModel.REF_TYPE_ROLE: 'ref_type',
    PlaylistItemModel.REF_ID_ROLE: 'ref_id',
    PlaylistItemModel.TITLE_ROLE: 'title',
    PlaylistItemModel.ARTIST_ROLE: 'artist',
    PlaylistItemModel.DURATION_ROLE: 'duration_sec',
}
