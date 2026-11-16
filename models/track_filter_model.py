"""Поисковый фильтр списка треков (колонка 2): title ИЛИ artist."""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import (
    QModelIndex,
    QObject,
    QSortFilterProxyModel,
    Qt,
    Property,
    Signal,
    Slot,
)

from models.track_model import TrackModel


class TrackFilterModel(QSortFilterProxyModel):
    """QSortFilterProxyModel с регистронезависимым поиском по двум ролям."""

    filterTextChanged = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._filter_text = ''
        self.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.setSortCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)

    def filterAcceptsRow(self, source_row: int,
                         source_parent: QModelIndex) -> bool:
        text = self._filter_text.casefold()
        if not text:
            return True
        source = self.sourceModel()
        if source is None:
            return True
        for role in (TrackModel.TITLE_ROLE, TrackModel.ARTIST_ROLE):
            value = source.data(source.index(source_row, 0, source_parent),
                                role)
            if value and text in str(value).casefold():
                return True
        return False

    @Slot(str)
    def setFilterText(self, text: str) -> None:
        if text == self._filter_text:
            return
        self._filter_text = text
        self.invalidate()  # заменяет устаревший invalidateFilter()
        self.filterTextChanged.emit()

    def filterText(self) -> str:
        return self._filter_text

    filterText = Property(str, filterText, setFilterText,
                          notify=filterTextChanged)

    @Slot(result=int)
    def count(self) -> int:
        """Число видимых треков (для подписи в QML)."""
        return self.rowCount()
