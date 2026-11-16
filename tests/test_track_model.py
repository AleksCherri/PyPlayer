"""Тесты модели треков (models/track_model.py) — не-GUI логика модели."""
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QByteArray, QModelIndex, Qt

from models.track_model import TrackModel
from services.db import Database
from services.library import scan_library


@pytest.fixture(scope='module')
def qapp() -> QCoreApplication:
    """QCoreApplication для QObject-логики (без GUI-платформы)."""
    return QCoreApplication.instance() or QCoreApplication([])


def _db_with_tracks(tmp_path: Path, make_wav) -> Database:
    db = Database(tmp_path / 'pyplayer.db')
    make_wav(tmp_path / 'music' / 'one.wav')
    make_wav(tmp_path / 'music' / 'two.wav')
    scan_library(db, tmp_path / 'music')
    return db


def test_row_count_and_roles(qapp: QCoreApplication, tmp_path: Path,
                             make_wav) -> None:
    db = _db_with_tracks(tmp_path, make_wav)
    try:
        model = TrackModel(db)
        model.reload()

        assert model.rowCount() == 2
        assert model.rowCount(QModelIndex()) == 2

        roles = model.roleNames()
        assert roles[TrackModel.TITLE_ROLE] == QByteArray(b'title')
        assert roles[TrackModel.ARTIST_ROLE] == QByteArray(b'artist')
    finally:
        db.close()


def test_data_roles(qapp: QCoreApplication, tmp_path: Path, make_wav) -> None:
    db = _db_with_tracks(tmp_path, make_wav)
    try:
        model = TrackModel(db)
        model.reload()

        index = model.index(0, 0)
        assert index.isValid()
        assert isinstance(model.data(index, TrackModel.ID_ROLE), int)
        assert model.data(index, TrackModel.TITLE_ROLE) in {'one', 'two'}
        assert model.data(index, TrackModel.FORMAT_ROLE) == 'WAV'
        assert model.data(index, TrackModel.SAMPLE_RATE_ROLE) == 8000
        # невалидный индекс / неизвестная роль -> None
        assert model.data(QModelIndex(), TrackModel.TITLE_ROLE) is None
        assert model.data(index, Qt.ItemDataRole.UserRole + 99) is None
    finally:
        db.close()


def test_reload_picks_up_new_tracks(qapp: QCoreApplication, tmp_path: Path,
                                    make_wav) -> None:
    db = Database(tmp_path / 'pyplayer.db')
    try:
        model = TrackModel(db)
        model.reload()
        assert model.rowCount() == 0

        make_wav(tmp_path / 'music' / 'new.wav')
        scan_library(db, tmp_path / 'music')
        model.reload()
        assert model.rowCount() == 1
    finally:
        db.close()
