"""Слой хранения PyPlayer: SQLite (stdlib sqlite3), схема v1, без ORM.

Стратегия управления соединениями (выбрана для будущей многопоточности —
аудио-движок и статистика пишут из других потоков):

- **Соединение на поток.** Каждое соединение хранится в
  ``threading.local`` и используется только из создавшего его потока
  (sqlite3-соединения по умолчанию не потокобезопасны). Поток получает
  соединение через ``get_connection()``; повторный вызов в том же потоке
  возвращает то же соединение. Закрывать соединение нужно из того же
  потока через ``close()``; не закрытые соединения освобождаются сборщиком
  мусора при завершении потока.
- **WAL-режим** — параллельные читатели не блокируют писателя.
- ``PRAGMA foreign_keys = ON`` и ``busy_timeout = 5000`` выставляются на
  каждое соединение (эти прагмы не сохраняются в файле БД).
- Групповые записи выполняются через ``transaction()`` (атомарно).
"""
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    path           TEXT    NOT NULL UNIQUE,
    fingerprint    TEXT    NOT NULL,
    title          TEXT    NOT NULL DEFAULT '',
    artist         TEXT    NOT NULL DEFAULT '',
    duration_sec   REAL    NOT NULL DEFAULT 0.0,
    sample_rate    INTEGER NOT NULL DEFAULT 0,
    format         TEXT    NOT NULL DEFAULT '',
    volume         REAL    NOT NULL DEFAULT 1.0,
    trim_start_sec REAL    NOT NULL DEFAULT 0.0,
    trim_end_sec   REAL    NOT NULL DEFAULT 0.0,
    speed          REAL    NOT NULL DEFAULT 1.0,
    pitch          REAL    NOT NULL DEFAULT 1.0,
    rate_mode      TEXT    NOT NULL DEFAULT 'both'
                   CHECK (rate_mode IN ('tempo_only', 'pitch_only', 'both'))
);

CREATE INDEX IF NOT EXISTS idx_tracks_fingerprint ON tracks (fingerprint);

CREATE TABLE IF NOT EXISTS play_stats (
    track_id           INTEGER PRIMARY KEY
                       REFERENCES tracks (id) ON DELETE CASCADE,
    play_count         INTEGER NOT NULL DEFAULT 0,
    total_listened_sec REAL    NOT NULL DEFAULT 0.0,
    last_played_at     TEXT
);

CREATE TABLE IF NOT EXISTS playlists (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT    NOT NULL UNIQUE,
    description   TEXT    NOT NULL DEFAULT '',
    created_at    TEXT    NOT NULL,
    loop          INTEGER NOT NULL DEFAULT 0,
    order_mode    TEXT    NOT NULL DEFAULT 'sequential'
                  CHECK (order_mode IN ('sequential', 'sequential_loop',
                                        'random_no_repeat', 'random_repeat',
                                        'random_loop_no_repeat')),
    allow_repeats INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS playlist_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    playlist_id INTEGER NOT NULL REFERENCES playlists (id) ON DELETE CASCADE,
    position    INTEGER NOT NULL,
    ref_type    TEXT    NOT NULL CHECK (ref_type IN ('track', 'playlist')),
    ref_id      INTEGER NOT NULL,
    UNIQUE (playlist_id, position)
);

CREATE TABLE IF NOT EXISTS migrations (
    schema_version INTEGER PRIMARY KEY,
    applied_at     TEXT    NOT NULL
);
"""


class Database:
    """Обёртка над sqlite3: потокобезопасное управление соединениями."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._local = threading.local()
        self._migrate()

    def get_connection(self) -> sqlite3.Connection:
        """Возвращает соединение текущего потока, создавая при первом вызове."""
        conn: sqlite3.Connection | None = getattr(self._local, 'conn', None)
        if conn is None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self._path, timeout=5.0)
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA foreign_keys = ON')
            conn.execute('PRAGMA journal_mode = WAL')
            conn.execute('PRAGMA busy_timeout = 5000')
            self._local.conn = conn
        return conn

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Атомарный блок: commit при успехе, rollback при исключении."""
        conn = self.get_connection()
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise

    def close(self) -> None:
        """Закрывает соединение текущего потока (если было создано)."""
        conn: sqlite3.Connection | None = getattr(self._local, 'conn', None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    def _migrate(self) -> None:
        """Создаёт таблицы (идемпотентно) и фиксирует версию схемы."""
        conn = self.get_connection()
        conn.executescript(_SCHEMA)
        row = conn.execute(
            'SELECT MAX(schema_version) FROM migrations').fetchone()
        current = row[0]
        if current is None:
            with self.transaction() as tx:
                tx.execute(
                    'INSERT INTO migrations (schema_version, applied_at) '
                    'VALUES (?, strftime(\'%Y-%m-%dT%H:%M:%fZ\', \'now\'))',
                    (SCHEMA_VERSION,),
                )
        elif current > SCHEMA_VERSION:
            raise RuntimeError(
                f'База данных версии {current} новее поддерживаемой '
                f'версии {SCHEMA_VERSION}')
