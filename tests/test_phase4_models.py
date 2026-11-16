"""Тесты Qt-моделей Phase 4 (не-GUI логика моделей и фильтра)."""
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QByteArray

from models.playlist_item_model import PlaylistItemModel
from models.playlist_model import PlaylistModel
from models.settings_model import SettingsModel
from models.track_filter_model import TrackFilterModel
from models.track_model import TrackModel
from services.config import load_config
from services.db import Database


@pytest.fixture(scope='module')
def qapp() -> QCoreApplication:
    return QCoreApplication.instance() or QCoreApplication([])


def _add_track(db: Database, title: str, artist: str) -> int:
    with db.transaction() as tx:
        cursor = tx.execute(
            'INSERT INTO tracks (path, fingerprint, title, artist) '
            'VALUES (?, ?, ?, ?)',
            (f'music/{title}.wav', f'fp-{title}', title, artist))
    return int(cursor.lastrowid)


def test_playlist_model_roles_and_mutations(qapp, db: Database) -> None:
    model = PlaylistModel(db)
    errors: list[str] = []
    model.errorOccurred.connect(errors.append)

    assert model.rowCount() == 0
    model.create('First', 'desc')
    assert model.rowCount() == 1
    index = model.index(0, 0)
    assert model.data(index, PlaylistModel.NAME_ROLE) == 'First'
    assert model.data(index, PlaylistModel.DESCRIPTION_ROLE) == 'desc'
    assert model.data(index, PlaylistModel.COUNT_ROLE) == 0
    assert model.roleNames()[PlaylistModel.NAME_ROLE] == QByteArray(b'name')

    model.create('First')  # дубль имени -> сигнал ошибки, без крэша
    assert len(errors) == 1
    assert model.rowCount() == 1

    playlist_id = model.data(index, PlaylistModel.ID_ROLE)
    model.rename(playlist_id, 'Renamed')
    assert model.data(model.index(0, 0), PlaylistModel.NAME_ROLE) == 'Renamed'
    model.remove(playlist_id)
    assert model.rowCount() == 0


def test_playlist_item_model_dnd_operations(qapp, db: Database) -> None:
    model = PlaylistModel(db)
    model.create('Items')
    playlist_id = model.data(model.index(0, 0), PlaylistModel.ID_ROLE)
    first = _add_track(db, 'one', 'A')
    second = _add_track(db, 'two', 'B')

    items = PlaylistItemModel(db)
    items.setPlaylist(playlist_id)
    assert items.rowCount() == 0
    items.addTrack(first)
    items.addTrack(second)
    assert items.rowCount() == 2
    assert items.data(items.index(0, 0),
                      PlaylistItemModel.REF_ID_ROLE) == first
    assert items.data(items.index(0, 0), PlaylistItemModel.TITLE_ROLE) == 'one'

    items.moveItem(1, 0)
    assert items.data(items.index(0, 0), PlaylistItemModel.REF_ID_ROLE) == \
        second
    items.removeAt(0)
    assert items.rowCount() == 1
    assert items.data(items.index(0, 0),
                      PlaylistItemModel.REF_ID_ROLE) == first

    items.setPlaylist(-1)
    assert items.rowCount() == 0


def test_track_filter_matches_title_and_artist(qapp, db: Database) -> None:
    _add_track(db, 'Imagine', 'Lennon')
    _add_track(db, 'Yesterday', 'Beatles')
    _add_track(db, 'Help', '')

    tracks = TrackModel(db)
    tracks.reload()
    proxy = TrackFilterModel()
    proxy.setSourceModel(tracks)
    assert proxy.rowCount() == 3
    assert proxy.count() == 3

    proxy.setFilterText('LENNON')  # регистронезависимый поиск по artist
    assert proxy.count() == 1
    assert proxy.data(proxy.index(0, 0), TrackModel.TITLE_ROLE) == 'Imagine'

    proxy.setFilterText('help')  # поиск по title
    assert proxy.count() == 1
    assert proxy.data(proxy.index(0, 0), TrackModel.TITLE_ROLE) == 'Help'

    proxy.setFilterText('')
    assert proxy.count() == 3


def test_settings_model_persists(qapp, tmp_path: Path) -> None:
    path = tmp_path / 'config.json'
    config = {
        'music_dir': 'music',
        'max_playlist_depth': 7,
        'smart_shuffle_default': 'off',
        'shuffle_temperature': 0.5,
    }
    settings = SettingsModel(path, config)
    assert settings.maxPlaylistDepth() == 7
    assert settings.smartShuffle == 'off'

    settings.setSmartShuffle('inverse')
    settings.setTemperature(1.25)
    assert settings.smartShuffle == 'inverse'
    assert settings.temperature == pytest.approx(1.25)

    saved = load_config(path)
    assert saved['smart_shuffle_default'] == 'inverse'
    assert saved['shuffle_temperature'] == pytest.approx(1.25)

    settings.setSmartShuffle('invalid')  # игнорируется
    assert settings.smartShuffle == 'inverse'

    settings.setTemperature(float('nan'))  # NaN не портит конфиг
    assert settings.temperature == pytest.approx(1.25)
    assert load_config(path)['shuffle_temperature'] == pytest.approx(1.25)
