"""Операции с плейлистами в БД (чистая логика, без Qt).

Плейлист — запись в ``playlists``; содержимое — строки ``playlist_items``
с позициями 0..N-1 и ref_type 'track' | 'playlist'. Все изменения позиций
идут в одной транзакции: сначала временные отрицательные позиции (чтобы
не нарушить UNIQUE(playlist_id, position)), затем целевые.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from typing import Any

from services.db import Database


class PlaylistError(ValueError):
    """Некорректная операция с плейлистом (неexists, дубль имени и т.п.)."""


def create_playlist(db: Database, name: str,
                    description: str = '') -> int:
    """Создаёт плейлист, возвращает его id; пустое/дубльное имя — ошибка."""
    name = name.strip()
    if not name:
        raise PlaylistError('Название плейлиста не может быть пустым')
    try:
        with db.transaction() as tx:
            cursor = tx.execute(
                "INSERT INTO playlists (name, description, created_at) "
                "VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))",
                (name, description),
            )
            return int(cursor.lastrowid)
    except sqlite3.IntegrityError as exc:
        raise PlaylistError(f'Плейлист «{name}» уже существует') from exc


def rename_playlist(db: Database, playlist_id: int, name: str) -> None:
    name = name.strip()
    if not name:
        raise PlaylistError('Название плейлиста не может быть пустым')
    try:
        with db.transaction() as tx:
            tx.execute('UPDATE playlists SET name = ? WHERE id = ?',
                       (name, playlist_id))
    except sqlite3.IntegrityError as exc:
        raise PlaylistError(f'Плейлист «{name}» уже существует') from exc


def set_description(db: Database, playlist_id: int,
                    description: str) -> None:
    with db.transaction() as tx:
        tx.execute('UPDATE playlists SET description = ? WHERE id = ?',
                   (description, playlist_id))


def set_order_mode(db: Database, playlist_id: int, mode: str) -> None:
    with db.transaction() as tx:
        tx.execute('UPDATE playlists SET order_mode = ? WHERE id = ?',
                   (mode, playlist_id))


def delete_playlist(db: Database, playlist_id: int) -> None:
    """Удаляет плейлист (.Items удаляются каскадом по FOREIGN KEY)."""
    with db.transaction() as tx:
        tx.execute('DELETE FROM playlists WHERE id = ?', (playlist_id,))


def add_item(db: Database, playlist_id: int, ref_type: str,
             ref_id: int, position: int | None = None) -> int:
    """Добавляет трек/плейлист; position=None — в конец.

    Возвращает id созданной строки playlist_items.
    """
    if ref_type not in ('track', 'playlist'):
        raise PlaylistError(f'Неизвестный ref_type: {ref_type!r}')
    with db.transaction() as tx:
        row = tx.execute(
            'SELECT MAX(position) FROM playlist_items WHERE playlist_id = ?',
            (playlist_id,)).fetchone()
        last = row[0]
        end = 0 if last is None else last + 1
        target = end if position is None else max(0, min(position, end))
        # Сдвигаем хвост в отрицательные позиции (неCollision с UNIQUE),
        # сохраняя исходный порядок; после вставки возвращаем на +
        shifted = [r['id'] for r in tx.execute(
            'SELECT id FROM playlist_items WHERE playlist_id = ? '
            'AND position >= ? ORDER BY position',
            (playlist_id, target)).fetchall()]
        _set_positions(tx, [(item_id, -i - 1)
                            for i, item_id in enumerate(shifted)])
        cursor = tx.execute(
            'INSERT INTO playlist_items (playlist_id, position, ref_type, '
            'ref_id) VALUES (?, ?, ?, ?)',
            (playlist_id, target, ref_type, ref_id),
        )
        item_id = int(cursor.lastrowid)
        for i, shifted_id in enumerate(shifted):
            tx.execute('UPDATE playlist_items SET position = ? WHERE id = ?',
                       (target + 1 + i, shifted_id))
        return item_id


def remove_item(db: Database, playlist_id: int, position: int) -> None:
    """Удаляет строку с позиции и уплотняет оставшиеся позиции."""
    with db.transaction() as tx:
        tx.execute('DELETE FROM playlist_items WHERE playlist_id = ? '
                   'AND position = ?', (playlist_id, position))
        ordered = [r['id'] for r in tx.execute(
            'SELECT id FROM playlist_items WHERE playlist_id = ? '
            'ORDER BY position', (playlist_id,)).fetchall()]
        _set_positions(tx, [(item_id, position)
                            for position, item_id in enumerate(ordered)])


def move_item(db: Database, playlist_id: int,
              from_position: int, to_position: int) -> None:
    """Переставляет элемент внутри плейлиста (для drag&drop)."""
    with db.transaction() as tx:
        ids = [r['id'] for r in tx.execute(
            'SELECT id FROM playlist_items WHERE playlist_id = ? '
            'ORDER BY position', (playlist_id,)).fetchall()]
        if not 0 <= from_position < len(ids) \
                or not 0 <= to_position < len(ids):
            raise PlaylistError('Позиция вне диапазона плейлиста')
        moved = ids.pop(from_position)
        ids.insert(to_position, moved)
        _set_positions(tx, [(item_id, position)
                            for position, item_id in enumerate(ids)])


def _set_positions(tx: sqlite3.Connection,
                   pairs: Sequence[tuple[int, int]]) -> None:
    """Двухфазная запись позиций (сначала в отрицательные)."""
    for item_id, _ in pairs:
        tx.execute('UPDATE playlist_items SET position = -position - 1 '
                   'WHERE id = ?', (item_id,))
    for item_id, position in pairs:
        tx.execute('UPDATE playlist_items SET position = ? WHERE id = ?',
                   (position, item_id))


def playlists_info(db: Database) -> list[dict[str, Any]]:
    """Плейлисты с количеством элементов, датой и описанием."""
    rows = db.get_connection().execute(
        'SELECT p.id, p.name, p.description, p.created_at, p.order_mode, '
        '  (SELECT COUNT(*) FROM playlist_items i '
        '   WHERE i.playlist_id = p.id) AS track_count '
        'FROM playlists p ORDER BY p.created_at, p.id').fetchall()
    return [dict(row) for row in rows]


def items_info(db: Database, playlist_id: int) -> list[dict[str, Any]]:
    """Элементы плейлиста с подписями (трек или вложенный плейлист)."""
    rows = db.get_connection().execute(
        'SELECT i.id, i.position, i.ref_type, i.ref_id, '
        '  CASE WHEN i.ref_type = \'track\' THEN t.title ELSE p.name END '
        '    AS title, '
        '  CASE WHEN i.ref_type = \'track\' THEN t.artist ELSE \'\' END '
        '    AS artist, '
        '  COALESCE(CASE WHEN i.ref_type = \'track\' '
        '                THEN t.duration_sec ELSE 0 END, 0) AS duration_sec '
        'FROM playlist_items i '
        'LEFT JOIN tracks t ON i.ref_type = \'track\' AND t.id = i.ref_id '
        'LEFT JOIN playlists p ON i.ref_type = \'playlist\' '
        '  AND p.id = i.ref_id '
        'WHERE i.playlist_id = ? ORDER BY i.position', (playlist_id,)
    ).fetchall()
    return [dict(row) for row in rows]


def playlist_stats(db: Database, playlist_id: int) -> dict[str, float]:
    """Статистика плейлиста для панели информации (todo.txt, п. 30)."""
    conn = db.get_connection()
    row = conn.execute(
        'SELECT COUNT(*) AS n, '
        '  COALESCE(SUM(t.duration_sec), 0) AS total, '
        '  COALESCE(MIN(t.duration_sec), 0) AS minimum, '
        '  COALESCE(MAX(t.duration_sec), 0) AS maximum '
        'FROM playlist_items i '
        'LEFT JOIN tracks t ON i.ref_type = \'track\' AND t.id = i.ref_id '
        'WHERE i.playlist_id = ?', (playlist_id,)).fetchone()
    listened_row = conn.execute(
        'SELECT COALESCE(SUM(s.total_listened_sec), 0) '
        'FROM play_stats s WHERE s.track_id IN ('
        '  SELECT ref_id FROM playlist_items WHERE playlist_id = ? '
        '  AND ref_type = \'track\')', (playlist_id,)).fetchone()
    all_row = conn.execute(
        'SELECT COALESCE(SUM(total_listened_sec), 0) FROM play_stats'
    ).fetchone()
    listened = float(listened_row[0])
    all_listened = float(all_row[0])
    count = int(row['n'])
    total = float(row['total'])
    return {
        'trackCount': float(count),
        'totalDurationSec': total,
        'avgDurationSec': total / count if count else 0.0,
        'minDurationSec': float(row['minimum']),
        'maxDurationSec': float(row['maximum']),
        'listenedSec': listened,
        'sharePercent': 100.0 * listened / all_listened
        if all_listened > 0 else 0.0,
    }
