"""Тесты JSON-экспорта (services/export.py)."""
import json
from contextlib import contextmanager
from pathlib import Path

import pytest

from services.db import Database, SCHEMA_VERSION
from services.export import export_to_json
from services.library import scan_library


def _playlist_with_item(db: Database, track_id: int) -> None:
    with db.transaction() as tx:
        cursor = tx.execute(
            'INSERT INTO playlists (name, description, created_at) '
            'VALUES (?, ?, ?)', ('Любимое', 'desc', '2026-01-01T00:00:00Z'))
        tx.execute(
            'INSERT INTO playlist_items (playlist_id, position, ref_type, '
            'ref_id) VALUES (?, ?, ?, ?)',
            (cursor.lastrowid, 0, 'track', track_id))


def test_export_contains_full_dump(db: Database, make_wav, tmp_path: Path) -> None:
    music = tmp_path / 'music'
    make_wav(music / 'song.wav')
    scan_library(db, music)
    track_id = db.get_connection().execute(
        'SELECT id FROM tracks').fetchone()['id']
    _playlist_with_item(db, track_id)

    out_path = tmp_path / 'backup' / 'export.json'
    export_to_json(db, out_path)

    data = json.loads(out_path.read_text(encoding='utf-8'))
    assert data['schema_version'] == SCHEMA_VERSION
    assert len(data['tracks']) == 1
    assert data['tracks'][0]['title'] == 'song'
    assert len(data['play_stats']) == 1
    assert data['playlists'][0]['name'] == 'Любимое'
    assert data['playlist_items'][0]['ref_type'] == 'track'
    assert data['playlist_items'][0]['ref_id'] == track_id


def test_export_empty_db(db: Database, tmp_path: Path) -> None:
    out_path = tmp_path / 'export.json'

    export_to_json(db, out_path)

    data = json.loads(out_path.read_text(encoding='utf-8'))
    assert data['tracks'] == []
    assert data['playlists'] == []
    assert data['play_stats'] == []
    assert data['playlist_items'] == []


def test_export_unicode_and_russian_content(db: Database, make_wav,
                                            tmp_path: Path) -> None:
    make_wav(tmp_path / 'музыка' / 'песня.wav')
    scan_library(db, tmp_path / 'музыка')

    out_path = tmp_path / 'export.json'
    export_to_json(db, out_path)

    raw = out_path.read_text(encoding='utf-8')
    assert 'песня' in raw                      # без \\u-эскейпов
    data = json.loads(raw)
    assert data['tracks'][0]['title'] == 'песня'


def test_export_reads_inside_open_transaction(
        db: Database, make_wav, tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch) -> None:
    """Чтение дампа идёт в открытой транзакции (один WAL-снимок)."""
    make_wav(tmp_path / 'music' / 'song.wav')
    scan_library(db, tmp_path / 'music')

    captured: dict[str, bool] = {}
    original_transaction = Database.transaction

    @contextmanager
    def spy_transaction(self: Database):
        with original_transaction(self) as conn:
            yield conn
            # выполняется после тела export_to_json, но до commit
            captured['in_transaction'] = conn.in_transaction

    monkeypatch.setattr(Database, 'transaction', spy_transaction)
    export_to_json(db, tmp_path / 'export.json')

    assert captured['in_transaction'] is True
