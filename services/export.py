"""Экспорт библиотеки в JSON (JSON — только резервная копия, БД первична)."""
import json
from pathlib import Path

from services.db import Database, SCHEMA_VERSION

_SELECTS: dict[str, str] = {
    'tracks': 'SELECT * FROM tracks ORDER BY id',
    'play_stats': 'SELECT * FROM play_stats ORDER BY track_id',
    'playlists': 'SELECT * FROM playlists ORDER BY id',
    'playlist_items': 'SELECT * FROM playlist_items ORDER BY playlist_id, position',
}


def export_to_json(db: Database, out_path: Path) -> Path:
    """Пишет полный дамп БД в ``out_path``; возвращает путь к файлу.

    Чтение всех таблиц — в одной отложенной транзакции, чтобы дамп был
    согласованным снимком (в WAL-режиме каждый autocommit-SELECT видел бы
    свою собственную версию данных).
    """
    with db.transaction() as conn:
        # sqlite3 в legacy-режиме не открывает BEGIN перед SELECT — делаем
        # это явно, иначе четыре SELECT идут в autocommit с разными
        # WAL-снимками и "транзакция" была бы пустым звуком.
        conn.execute('BEGIN')
        data = {
            'schema_version': SCHEMA_VERSION,
            **{name: [dict(row) for row in conn.execute(sql)]
               for name, sql in _SELECTS.items()},
        }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open('w', encoding='utf-8') as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)
        fp.write('\n')
    return out_path
