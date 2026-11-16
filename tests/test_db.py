"""Тесты слоя хранения (services/db.py)."""
import sqlite3
import threading
from pathlib import Path

import pytest

from services.db import SCHEMA_VERSION, Database

EXPECTED_TABLES = {'tracks', 'play_stats', 'playlists', 'playlist_items',
                   'migrations'}


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {row[0] for row in rows}


def test_schema_v1_tables_created(db: Database) -> None:
    assert EXPECTED_TABLES <= _table_names(db.get_connection())


def test_migrations_records_schema_version(db: Database) -> None:
    row = db.get_connection().execute(
        'SELECT schema_version, applied_at FROM migrations').fetchone()
    assert row['schema_version'] == SCHEMA_VERSION
    assert row['applied_at']


def test_migrate_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / 'pyplayer.db'
    first = Database(db_path)
    second = Database(db_path)  # повторное открытие той же БД
    rows = second.get_connection().execute(
        'SELECT COUNT(*) FROM migrations').fetchall()
    assert len(rows) == 1
    first.close()
    second.close()


def test_pragmas_on_every_connection(db: Database) -> None:
    conn = db.get_connection()
    assert conn.execute('PRAGMA foreign_keys').fetchone()[0] == 1
    assert conn.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
    assert conn.execute('PRAGMA busy_timeout').fetchone()[0] == 5000


def test_same_thread_reuses_connection(db: Database) -> None:
    assert db.get_connection() is db.get_connection()


def test_other_thread_gets_own_connection(db: Database) -> None:
    holder: dict[str, object] = {}

    def worker() -> None:
        conn = db.get_connection()
        holder['conn'] = conn
        holder['foreign_keys'] = conn.execute(
            'PRAGMA foreign_keys').fetchone()[0]

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()

    assert holder['conn'] is not db.get_connection()
    assert holder['foreign_keys'] == 1


def test_transaction_rolls_back_on_error(db: Database) -> None:
    with pytest.raises(RuntimeError):
        with db.transaction() as tx:
            tx.execute("INSERT INTO tracks (path, fingerprint) VALUES ('a', 'f')")
            raise RuntimeError('откат')

    count = db.get_connection().execute('SELECT COUNT(*) FROM tracks').fetchone()[0]
    assert count == 0


def test_play_stats_foreign_key_enforced(db: Database) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        with db.transaction() as tx:
            tx.execute('INSERT INTO play_stats (track_id) VALUES (999)')


def test_database_newer_than_supported(tmp_path: Path) -> None:
    db_path = tmp_path / 'pyplayer.db'
    db = Database(db_path)
    with db.transaction() as tx:
        tx.execute('UPDATE migrations SET schema_version = ?',
                   (SCHEMA_VERSION + 1,))
    db.close()

    with pytest.raises(RuntimeError, match='новее'):
        Database(db_path)
