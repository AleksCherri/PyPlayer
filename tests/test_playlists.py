"""Тесты операций с плейлистами в БД (services/playlists.py)."""
from pathlib import Path

import pytest

from services.db import Database
from services.playlists import (
    PlaylistError,
    add_item,
    create_playlist,
    delete_playlist,
    items_info,
    move_item,
    playlists_info,
    playlist_stats,
    remove_item,
    rename_playlist,
    set_description,
    set_order_mode,
)


def _add_track(db: Database, name: str, duration: float) -> int:
    with db.transaction() as tx:
        cursor = tx.execute(
            'INSERT INTO tracks (path, fingerprint, title, duration_sec) '
            'VALUES (?, ?, ?, ?)',
            (f'music/{name}.wav', f'fp-{name}', name, duration))
        track_id = int(cursor.lastrowid)
        tx.execute('INSERT INTO play_stats (track_id) VALUES (?)',
                   (track_id,))
    return track_id


def test_create_rename_delete_playlist(db: Database) -> None:
    playlist_id = create_playlist(db, 'Rock', 'Любимый жанр')
    rename_playlist(db, playlist_id, 'Indie')
    set_description(db, playlist_id, 'Новое описание')
    set_order_mode(db, playlist_id, 'random_no_repeat')

    rows = playlists_info(db)
    assert len(rows) == 1
    assert rows[0]['name'] == 'Indie'
    assert rows[0]['description'] == 'Новое описание'
    assert rows[0]['order_mode'] == 'random_no_repeat'
    assert rows[0]['created_at']

    delete_playlist(db, playlist_id)
    assert playlists_info(db) == []


def test_create_rejects_empty_and_duplicate_names(db: Database) -> None:
    first = create_playlist(db, 'Same')
    other = create_playlist(db, 'Other')
    with pytest.raises(PlaylistError):
        create_playlist(db, '   ')
    with pytest.raises(PlaylistError):
        create_playlist(db, 'Same')
    with pytest.raises(PlaylistError):
        rename_playlist(db, first, 'Other')
    rename_playlist(db, first, 'Renamed')
    assert {row['name'] for row in playlists_info(db)} == {'Renamed', 'Other'}
    assert other != first


def test_add_item_appends_and_inserts(db: Database) -> None:
    playlist_id = create_playlist(db, 'P')
    t1 = _add_track(db, 'a', 10.0)
    t2 = _add_track(db, 'b', 20.0)
    t3 = _add_track(db, 'c', 30.0)

    assert add_item(db, playlist_id, 'track', t1) > 0
    assert add_item(db, playlist_id, 'track', t3) > 0
    # Вставка в середину сдвигает хвост, позиции остаются уникальными.
    add_item(db, playlist_id, 'track', t2, position=1)

    rows = items_info(db, playlist_id)
    assert [row['ref_id'] for row in rows] == [t1, t2, t3]
    assert [row['position'] for row in rows] == [0, 1, 2]
    assert playlists_info(db)[0]['track_count'] == 3


def test_move_and_remove_item_keep_positions_dense(db: Database) -> None:
    playlist_id = create_playlist(db, 'P')
    ids = [_add_track(db, name, 5.0) for name in ('a', 'b', 'c', 'd')]
    for track_id in ids:
        add_item(db, playlist_id, 'track', track_id)

    move_item(db, playlist_id, 3, 0)
    assert [row['ref_id'] for row in items_info(db, playlist_id)] == \
        [ids[3], ids[0], ids[1], ids[2]]
    remove_item(db, playlist_id, 1)
    rows = items_info(db, playlist_id)
    assert [row['ref_id'] for row in rows] == [ids[3], ids[1], ids[2]]
    assert [row['position'] for row in rows] == [0, 1, 2]

    with pytest.raises(PlaylistError):
        move_item(db, playlist_id, 0, 99)


def test_items_info_join_track_and_nested_playlist(db: Database) -> None:
    inner = create_playlist(db, 'Inner')
    outer = create_playlist(db, 'Outer')
    track_id = _add_track(db, 'song', 42.0)
    add_item(db, outer, 'track', track_id)
    add_item(db, outer, 'playlist', inner)

    rows = items_info(db, outer)
    assert rows[0]['title'] == 'song'
    assert rows[0]['ref_type'] == 'track'
    assert rows[0]['duration_sec'] == 42.0
    assert rows[1]['title'] == 'Inner'
    assert rows[1]['ref_type'] == 'playlist'
    assert rows[1]['duration_sec'] == 0.0


def test_playlist_stats(db: Database) -> None:
    playlist_id = create_playlist(db, 'Stats')
    short = _add_track(db, 'short', 60.0)
    long = _add_track(db, 'long', 180.0)
    _add_track(db, 'not-in-playlist', 100.0)
    add_item(db, playlist_id, 'track', short)
    add_item(db, playlist_id, 'track', long)
    with db.transaction() as tx:
        tx.execute('UPDATE play_stats SET total_listened_sec = 30 '
                   'WHERE track_id = ?', (short,))
        tx.execute('UPDATE play_stats SET total_listened_sec = 90 '
                   'WHERE track_id = ?', (long,))
        tx.execute('UPDATE play_stats SET total_listened_sec = 80 '
                   'WHERE track_id NOT IN (?, ?)', (short, long))

    stats = playlist_stats(db, playlist_id)
    assert stats['trackCount'] == 2
    assert stats['totalDurationSec'] == 240.0
    assert stats['avgDurationSec'] == 120.0
    assert stats['minDurationSec'] == 60.0
    assert stats['maxDurationSec'] == 180.0
    assert stats['listenedSec'] == 120.0
    assert stats['sharePercent'] == pytest.approx(60.0)  # 120 / 200


def test_playlist_stats_empty(db: Database) -> None:
    playlist_id = create_playlist(db, 'Empty')
    stats = playlist_stats(db, playlist_id)
    assert stats['trackCount'] == 0
    assert stats['avgDurationSec'] == 0.0
    assert stats['sharePercent'] == 0.0


def test_playlist_stats_zero_total_listened(db: Database) -> None:
    """Треки есть, прослушиваний нет: деление на ноль не происходит."""
    playlist_id = create_playlist(db, 'Silent')
    track_id = _add_track(db, 'silent', 12.0)
    add_item(db, playlist_id, 'track', track_id)

    stats = playlist_stats(db, playlist_id)

    assert stats['trackCount'] == 1
    assert stats['totalDurationSec'] == 12.0
    assert stats['listenedSec'] == 0.0
    assert stats['sharePercent'] == 0.0
