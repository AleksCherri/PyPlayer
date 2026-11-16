"""Сканер музыкальной библиотеки.

Рекурсивный обход настроенной папки музыки (.mp3/.flac/.wav), upsert
треков в БД с метаданными mutagen. Карточка трека создаётся лениво — при
первом появлении файла в скане. Переименование файла переживает скан:
сначала совпадение по path, затем по fingerprint («сироты» — строки, чей
path не встретился в текущем скане).
"""
import hashlib
import logging
import sqlite3
from collections import deque
from pathlib import Path
from typing import Any

import mutagen
from mutagen.flac import FLAC
from mutagen.mp3 import MP3
from mutagen.wave import WAVE

from services.db import Database

logger = logging.getLogger(__name__)

AUDIO_EXTENSIONS = frozenset({'.mp3', '.flac', '.wav'})
_FINGERPRINT_CHUNK = 64 * 1024

_EXTENSION_READERS: dict[str, Any] = {
    '.mp3': MP3,
    '.wav': WAVE,
    '.flac': FLAC,
}

_ID3_FRAMES = {'title': 'TIT2', 'artist': 'TPE1'}


def resolve_music_dir(music_dir: str | None, base_dir: Path) -> Path | None:
    """Относительный ``music_dir`` из конфига -> путь от ``base_dir``.

    Пустое/некорректное значение даёт None (скан пропускается).
    """
    if not music_dir:
        return None
    path = Path(music_dir)
    return path if path.is_absolute() else base_dir / path


def compute_fingerprint(path: Path) -> str:
    """Размер + mtime (нс) + SHA-256 первых 64 КиБ файла."""
    stat = path.stat()
    hasher = hashlib.sha256()
    with path.open('rb') as fp:
        hasher.update(fp.read(_FINGERPRINT_CHUNK))
    return f'{stat.st_size}:{stat.st_mtime_ns}:{hasher.hexdigest()}'


def _first_tag(audio: Any, key: str, fallback: str) -> str:
    """Первый элемент easy-тега mutagen или запасное значение."""
    try:
        values = audio.get(key)
    except Exception:  # не-easy FileType: ключи-фреймы вместо 'title'
        values = None
    if values:
        return str(values[0])
    tags = getattr(audio, 'tags', None)
    if tags is not None and hasattr(tags, 'getall'):  # ID3 (MP3/WAVE)
        frames = tags.getall(_ID3_FRAMES.get(key, key))
        if frames and getattr(frames[0], 'text', None):
            return str(frames[0].text[0])
    return fallback


def _open_audio(path: Path) -> Any:
    """Открывает файл: сначала автодетект mutagen.File, затем по расширению.

    Автодетект не справляется с некоторыми реальными файлами (например,
    RIFF/WAVE с нестандартным ID3-блоком распознаётся как mp3), поэтому
    при неудаче пробуем явный класс по расширению. Ошибка конструктора
    означает битый файл — сканер пропустит его с логом.
    """
    try:
        audio = mutagen.File(path, easy=True)
        if audio is not None:
            return audio
    except Exception:
        pass
    reader = _EXTENSION_READERS.get(path.suffix.lower())
    if reader is None:
        return None
    return reader(str(path))


def read_metadata(path: Path) -> dict[str, Any]:
    """Читает метаданные mutagen; бросает исключение для битых файлов."""
    audio = _open_audio(path)
    if audio is None:
        raise ValueError('не распознан как аудиофайл')
    info = audio.info
    return {
        'title': _first_tag(audio, 'title', path.stem),
        'artist': _first_tag(audio, 'artist', ''),
        'duration_sec': float(info.length),
        'sample_rate': int(getattr(info, 'sample_rate', 0)),
        'format': path.suffix[1:].upper(),
    }


def fetch_tracks(db: Database) -> list[sqlite3.Row]:
    """Все треки для UI-модели (стабильный порядок)."""
    return db.get_connection().execute(
        'SELECT id, path, title, artist, duration_sec, sample_rate, format '
        'FROM tracks ORDER BY title COLLATE NOCASE, path'
    ).fetchall()


def _take_orphan(orphans: dict[str, deque[int]],
                 fingerprint: str) -> int | None:
    """Достаёт одного сироту по fingerprint; каждый id употребляется один раз."""
    queue = orphans.get(fingerprint)
    if not queue:
        return None
    orphan_id = queue.popleft()
    if not queue:
        del orphans[fingerprint]
    return orphan_id


def scan_library(db: Database, music_dir: Path | None) -> int:
    """Сканирует ``music_dir`` и upsert'ит треки; возвращает число файлов.

    Отсутствующая или не заданная папка обрабатывается мягко: скан
    пропускается, библиотека остаётся без изменений, возвращается 0.
    Строки-«сироты» (файл удалён/переименован) из БД не удаляются:
    переименованный файл опознаётся по fingerprint.
    """
    if music_dir is None or not music_dir.is_dir():
        logger.warning('Папка музыки не найдена: %s — скан пропущен', music_dir)
        return 0

    files = sorted(
        path for path in music_dir.rglob('*')
        if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS
    )

    processed = 0
    with db.transaction() as tx:
        rows = tx.execute(
            'SELECT id, path, fingerprint FROM tracks').fetchall()
        scanned_paths = {str(file.resolve()) for file in files}
        known_paths = {row['path'] for row in rows}
        # fingerprint -> очередь id сирот: дубликаты отпечатков не теряются,
        # каждый сирота может быть употреблён не более одного раза.
        orphans_by_fingerprint: dict[str, deque[int]] = {}
        for row in rows:
            if row['path'] not in scanned_paths:
                orphans_by_fingerprint.setdefault(
                    row['fingerprint'], deque()).append(row['id'])

        for file in files:
            path_str = str(file.resolve())
            try:
                fingerprint = compute_fingerprint(file)
                metadata = read_metadata(file)
            except Exception as exc:  # mutagen: битый/непонятный файл
                logger.warning('Файл пропущен: %s — %s', file, exc)
                continue

            values = (fingerprint, metadata['title'], metadata['artist'],
                      metadata['duration_sec'], metadata['sample_rate'],
                      metadata['format'])

            if path_str in known_paths:
                # карточка уже есть по path — обновляем метаданные
                tx.execute(
                    'UPDATE tracks SET fingerprint = ?, title = ?, '
                    'artist = ?, duration_sec = ?, sample_rate = ?, '
                    'format = ? WHERE path = ?',
                    (*values, path_str),
                )
            elif (orphan_id := _take_orphan(orphans_by_fingerprint,
                                            fingerprint)) is not None:
                # файл переименован/перенесён: fingerprint совпал с сиротой
                tx.execute(
                    'UPDATE tracks SET path = ?, fingerprint = ?, '
                    'title = ?, artist = ?, duration_sec = ?, '
                    'sample_rate = ?, format = ? WHERE id = ?',
                    (path_str, *values, orphan_id),
                )
            else:
                # ленивое создание карточки при первом появлении файла
                cursor = tx.execute(
                    'INSERT INTO tracks (path, fingerprint, title, artist, '
                    'duration_sec, sample_rate, format) '
                    'VALUES (?, ?, ?, ?, ?, ?, ?)', (path_str, *values))
                tx.execute('INSERT INTO play_stats (track_id) VALUES (?)',
                           (cursor.lastrowid,))
            processed += 1

    return processed
