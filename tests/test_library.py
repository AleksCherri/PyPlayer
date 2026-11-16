"""Тесты сканера библиотеки (services/library.py) на wav из stdlib `wave`."""
import json
import logging
import os
from pathlib import Path

import pytest

from services.db import Database
from services.library import (
    compute_fingerprint,
    resolve_music_dir,
    scan_library,
)


def _track_count(db: Database) -> int:
    return db.get_connection().execute('SELECT COUNT(*) FROM tracks').fetchone()[0]


def test_empty_dir_scan(db: Database, tmp_path: Path) -> None:
    music = tmp_path / 'music'
    music.mkdir()

    assert scan_library(db, music) == 0
    assert _track_count(db) == 0


def test_missing_dir_is_handled_gracefully(db: Database, tmp_path: Path) -> None:
    missing = tmp_path / 'nope'

    assert scan_library(db, missing) == 0
    assert scan_library(db, None) == 0
    assert _track_count(db) == 0


def test_resolve_music_dir_empty_is_none(tmp_path: Path) -> None:
    assert resolve_music_dir('', tmp_path) is None
    assert resolve_music_dir(None, tmp_path) is None
    assert resolve_music_dir('music', tmp_path) == tmp_path / 'music'
    absolute = tmp_path / 'abс' / 'музыка'  # non-ASCII путь
    assert resolve_music_dir(str(absolute), tmp_path) == absolute


def test_scan_creates_lazy_cards_with_metadata(db: Database, make_wav,
                                               tmp_path: Path) -> None:
    wav = make_wav(tmp_path / 'music' / 'song.wav', duration=0.5, rate=8000)

    assert scan_library(db, tmp_path / 'music') == 1

    row = db.get_connection().execute('SELECT * FROM tracks').fetchone()
    assert row['title'] == 'song'          # без тегов -> имя файла
    assert row['artist'] == ''
    assert row['duration_sec'] == pytest.approx(0.5, abs=0.01)
    assert row['sample_rate'] == 8000
    assert row['format'] == 'WAV'
    # ленивая карточка: строка статистики создана вместе с треком
    stats = db.get_connection().execute(
        'SELECT track_id FROM play_stats').fetchall()
    assert [s['track_id'] for s in stats] == [row['id']]


def test_scan_is_recursive_and_filters_extensions(db: Database, make_wav,
                                                  tmp_path: Path) -> None:
    music = tmp_path / 'music'
    make_wav(music / 'a' / 'deep.wav')
    make_wav(music / 'B.WAV')                       # расширение в верхнем регистре
    (music / 'notes.txt').write_text('не аудио', encoding='utf-8')
    (music / 'image.jpg').write_bytes(b'\xff\xd8\xff\xe0')

    assert scan_library(db, music) == 2
    assert _track_count(db) == 2


def test_rescan_is_idempotent(db: Database, make_wav, tmp_path: Path) -> None:
    make_wav(tmp_path / 'music' / 'one.wav')
    make_wav(tmp_path / 'music' / 'two.wav')

    scan_library(db, tmp_path / 'music')
    rows_before = db.get_connection().execute(
        'SELECT id, path FROM tracks ORDER BY id').fetchall()

    scan_library(db, tmp_path / 'music')

    rows_after = db.get_connection().execute(
        'SELECT id, path FROM tracks ORDER BY id').fetchall()
    assert rows_after == rows_before                # нет дубликатов строк
    stats = db.get_connection().execute(
        'SELECT COUNT(*) FROM play_stats').fetchone()[0]
    assert stats == 2


def test_updated_file_refreshes_fingerprint_same_path(
        db: Database, make_wav, tmp_path: Path) -> None:
    """Ветка UPDATE по известному path: тот же id, отпечаток обновлён."""
    music = tmp_path / 'music'
    wav = make_wav(music / 'song.wav')
    scan_library(db, music)
    before = db.get_connection().execute(
        'SELECT id, fingerprint FROM tracks').fetchone()

    os.utime(wav, (1_700_000_000, 1_700_000_000))  # меняет mtime -> fingerprint
    scan_library(db, music)

    after = db.get_connection().execute(
        'SELECT id, fingerprint FROM tracks').fetchall()
    assert len(after) == 1                          # дубликата нет
    assert after[0]['id'] == before['id']           # та же карточка
    assert after[0]['fingerprint'] != before['fingerprint']


def test_retagged_file_updates_title_same_path(db: Database, tmp_path: Path) -> None:
    """Ветка UPDATE по известному path: теги перечитываются, id не меняется."""
    music = tmp_path / 'music'
    music.mkdir(parents=True, exist_ok=True)
    path = music / 'tagged.mp3'
    frame = b'\xff\xfb\x90\x00' + b'\x00' * 413  # минимальный MPEG-1 L3 фрейм
    path.write_bytes(frame * 38)

    from mutagen.id3 import ID3, TIT2
    tags = ID3()
    tags.add(TIT2(encoding=3, text='Старое имя'))
    tags.save(str(path), v2_version=3)
    scan_library(db, music)
    before = db.get_connection().execute(
        'SELECT id, title FROM tracks').fetchone()
    assert before['title'] == 'Старое имя'

    tags = ID3(str(path))
    tags.add(TIT2(encoding=3, text='Новое имя'))
    tags.save(str(path), v2_version=3)
    scan_library(db, music)

    after = db.get_connection().execute(
        'SELECT id, title FROM tracks').fetchall()
    assert len(after) == 1
    assert after[0]['id'] == before['id']
    assert after[0]['title'] == 'Новое имя'


def test_rename_survives_via_fingerprint(db: Database, make_wav,
                                         tmp_path: Path) -> None:
    music = tmp_path / 'music'
    wav = make_wav(music / 'old_name.wav')
    scan_library(db, music)
    id_before = db.get_connection().execute(
        'SELECT id FROM tracks').fetchone()['id']

    renamed = wav.rename(music / 'new_name.wav')
    scan_library(db, music)

    row = db.get_connection().execute(
        'SELECT id, path, title FROM tracks').fetchall()
    assert len(row) == 1                            # дубликат не создан
    assert row[0]['id'] == id_before                # та же карточка
    assert row[0]['path'] == str(renamed.resolve())
    assert row[0]['title'] == 'new_name'


def test_corrupt_file_skipped_with_log(db: Database, tmp_path: Path,
                                       caplog: pytest.LogCaptureFixture) -> None:
    bad = tmp_path / 'music' / 'broken.mp3'
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_bytes(b'\x00\x01\x02 garbage not an mp3')

    with caplog.at_level(logging.WARNING, logger='services.library'):
        processed = scan_library(db, tmp_path / 'music')

    assert processed == 0
    assert _track_count(db) == 0
    assert 'broken.mp3' in caplog.text


def test_fingerprint_is_stable_and_uses_64kib(make_wav, tmp_path: Path) -> None:
    wav = make_wav(tmp_path / 'x.wav')
    first = compute_fingerprint(wav)
    assert first == compute_fingerprint(wav)        # повторный вызов стабилен
    size, mtime, digest = first.split(':')
    assert int(size) > 0
    assert len(digest) == 64                        # sha256 hex


def test_corrupt_and_valid_files_mixed(db: Database, make_wav,
                                       tmp_path: Path) -> None:
    music = tmp_path / 'music'
    make_wav(music / 'good.wav')
    (music / 'bad.flac').write_bytes(b'not really flac')

    assert scan_library(db, music) == 1
    assert _track_count(db) == 1


def test_unicode_paths_and_metadata(db: Database, make_wav,
                                    tmp_path: Path) -> None:
    make_wav(tmp_path / 'музыка' / 'песня.wav')

    assert scan_library(db, tmp_path / 'музыка') == 1
    row = db.get_connection().execute('SELECT path, title FROM tracks').fetchone()
    assert row['title'] == 'песня'
    assert 'песня.wav' in row['path']


def test_metadata_tags_are_read(db: Database, tmp_path: Path) -> None:
    """Теги (title/artist) читаются mutagen: mp3 с ID3 (минимальные фреймы)."""
    music = tmp_path / 'music'
    music.mkdir(parents=True, exist_ok=True)
    path = music / 'tagged.mp3'

    # минимальный валидный mp3: MPEG-1 Layer III, 128 kbps, 44100 Hz,
    # frame = 144 * 128000 / 44100 = 417 байт; ~38 фреймов ≈ 1 сек
    frame = b'\xff\xfb\x90\x00' + b'\x00' * 413
    path.write_bytes(frame * 38)

    from mutagen.id3 import ID3, TIT2, TPE1
    tags = ID3()
    tags.add(TIT2(encoding=3, text='Заголовок'))
    tags.add(TPE1(encoding=3, text='Исполнитель'))
    tags.save(str(path), v2_version=3)

    assert scan_library(db, music) == 1
    row = db.get_connection().execute(
        'SELECT title, artist, sample_rate FROM tracks').fetchone()
    assert row['title'] == 'Заголовок'
    assert row['artist'] == 'Исполнитель'
    assert row['sample_rate'] == 44100


def test_json_music_dir_config_value(db: Database, tmp_path: Path) -> None:
    """Интеграция с конфигом: music_dir из config.json резолвится и сканируется."""
    config_path = tmp_path / 'data' / 'config.json'
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps({"music_dir": "music"}), encoding='utf-8')

    from services.config import load_config
    config = load_config(config_path)
    music_dir = resolve_music_dir(config['music_dir'], tmp_path)
    music_dir.mkdir()
    assert scan_library(db, music_dir) == 0         # пустая папка — без ошибок
