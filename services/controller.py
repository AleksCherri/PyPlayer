"""Контроллер воспроизведения PyPlayer: связывает движок, очередь и БД.

Обязанности (только GUI-поток; все слоты вызываются в GUI-потоке):

- **Управление**: play (трек по id), play_playlist, pause/resume/stop,
  next/prev, громкость (passthrough в движок).
- **Очередь**: плейлист разворачивается services.queue.flatten_playlist
  (защита от циклов и глубины), порядок — services.queue.PlaybackQueue
  с одним из 5 режимов; seed инъецируется для тестов.
- **Статистика**: движок копит реально прослушанные секунды в памяти;
  контроллер сбрасывает их в play_stats ТОЛЬКО в моменты конца трека,
  переключения (next/prev/play другого трека) и выхода из приложения —
  во время воспроизведения в БД не пишется ничего.

Правило play_count (зафиксировано): play_count увеличивается на 1 тогда
и только тогда, когда played_seconds > 0 на момент сброса. Трек,
переключённый с 0 прослушанных секунд (мгновенный next), счётчик не
увеличивает и вообще не создаёт/не обновляет строку (запись += 0
бессмысленна, лишняя транзакция не нужна). total_listened_sec всегда
прибавляет played_seconds (> 0 в момент записи), last_played_at
обновляется той же транзакцией (один upsert на сброс).

Ошибки: сбой декодирования трека (AudioEngineError) при автовыпереде
пропускается — берётся следующий; при ручном play() исключение
пробрасывается вызывающему (UI может показать сообщение). Циклы/глубина
плейлистов перехватываются — воспроизведение не начинается, крэша нет.
"""
from __future__ import annotations

import math
import random
from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from services.audio.engine import (
    AudioEngineError,
    EventBridge,
    PlaybackEngine,
    PlaybackTrack,
    TrackFinished,
)
from services.db import Database
from services.queue import (
    ORDER_MODES,
    ORDER_RANDOM_LOOP_NO_REPEAT,
    ORDER_RANDOM_NO_REPEAT,
    ORDER_RANDOM_REPEAT,
    PlaylistCycleError,
    PlaylistDepthError,
    PlaybackQueue,
    PlaylistInfo,
    flatten_playlist,
)
from services.shuffle import (
    DEFAULT_RECENCY_WINDOW_DAYS,
    DEFAULT_TEMPERATURE,
    SMART_MODES,
    SMART_OFF,
    TrackScore,
    weighted_shuffle,
)

_RANDOM_MODES = frozenset({ORDER_RANDOM_NO_REPEAT, ORDER_RANDOM_REPEAT,
                           ORDER_RANDOM_LOOP_NO_REPEAT})


class PlaybackController(QObject):
    """Слой между QML, аудио-движком и БД."""

    currentTrackChanged = Signal(dict)   # карточка текущего трека | {} (нет)
    positionChanged = Signal(float, float)  # позиция, длительность (сек)
    playlistChanged = Signal(int, str)   # playlist_id, name текущего

    def __init__(self, db: Database, engine: PlaybackEngine,
                 parent: QObject | None = None, max_depth: int = 5,
                 seed: int | None = None, poll_ms: int = 250) -> None:
        super().__init__(parent)
        self._db = db
        self._engine = engine
        #Phase 4: передавать int(load_config(...)['max_playlist_depth'])
        self._max_depth = max_depth
        self._rng = random.Random(seed)
        self._smart_mode = SMART_OFF
        self._smart_temperature = DEFAULT_TEMPERATURE
        self._recency_window_days = DEFAULT_RECENCY_WINDOW_DAYS
        self._queue: PlaybackQueue | None = None
        self._current_track: dict | None = None   # строка tracks как dict
        self._stats_flushed = False     # один сброс на один текущий трек
        self._playlist_id: int | None = None
        self._playlist_name: str = ''

        # Мост очереди событий движка (GUI-поток); публичный для тестов.
        self.bridge = EventBridge(engine, self)
        self.bridge.track_finished.connect(self._on_track_finished)

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(poll_ms)
        self._poll_timer.timeout.connect(self._poll_position)
        self._poll_timer.start()

    # --- публичное API ---

    @property
    def current_track(self) -> dict | None:
        """Карточка текущего трека (строка tracks) или None."""
        return self._current_track

    @Slot(int)
    def play(self, track_id: int) -> None:
        """Играть трек по id из библиотеки.

        Прошлый трек сбрасывается в статистику (если слушался), затем
        трек загружается и воспроизводится. AudioEngineError пробрасывается
        (битый/отсутствующий файл) — прошлый трек при этом уже сброшен.
        """
        row = self._fetch_track_row(track_id)
        if row is None:
            raise ValueError(f'Трек {track_id} отсутствует в библиотеке')
        # Одиночный трек не продолжается очередью плейлиста по окончании.
        self._queue = None
        self._switch_to_row(row)
        self._playlist_id = None
        self._playlist_name = ''

    @Slot(int)
    def play_playlist(self, playlist_id: int) -> None:
        """Играть плейлист: склейка -> очередь с режимом -> первый трек.

        Пустой плейлист: воспроизведение не начинается (без крэша).
        Цикл/превышение глубины: PlaylistCycleError/PlaylistDepthError
        перехватываются, ничего не играет.
        """
        # Сброс прошлого трека обязателен и при пустом/битом плейлисте:
        # это всё равно переключение (выбор нового источника).
        self._flush_current()
        self._queue = None
        track_ids = self._flatten_playlist_from_db(playlist_id)
        if not track_ids:
            self._stop_playback()
            return
        info = self._fetch_playlist_info(playlist_id)
        if info is None or info.order_mode not in ORDER_MODES:
            self._stop_playback()
            return
        self._queue = self._make_queue(track_ids, info.order_mode)
        self._playlist_id = playlist_id
        self._playlist_name = info.name
        self.playlistChanged.emit(playlist_id, info.name)
        self._advance()

    @Slot()
    def pause(self) -> None:
        """Пауза (аккумулятор движка замирает — в статистику не пишем)."""
        self._engine.pause()

    @Slot()
    def resume(self) -> None:
        """Продолжить после паузы."""
        self._engine.resume()

    @Slot()
    def stop(self) -> None:
        """Остановка: текущий трек сбрасывается в статистику."""
        self._flush_current()
        self._stop_playback()

    @Slot()
    def next_track(self) -> None:
        """Следующий трек по режиму порядка (None в конце — стоп)."""
        if self._queue is None:
            return
        self._flush_current()
        self._advance()

    @Slot()
    def prev_track(self) -> None:
        """Предыдущий трек по режиму порядка (None в начале — ничего).

        Сначала спрашиваем очередь: если previous отсутствует, трек НЕ
        меняется — значит и сбрасывать его статистику нельзя (иначе
        последующее прослушивание не попадёт в БД из-за _stats_flushed).
        prev() меняет _pos только при успехе, поэтому сброс после него
        безопасен.
        """
        if self._queue is None:
            return
        track_id = self._queue.prev()
        if track_id is None:
            return
        self._flush_current()
        self._start_track_id(track_id)

    @Slot(float)
    def set_volume(self, volume: float) -> None:
        """Громкость 0..1 (passthrough в движок; NaN игнорируется им)."""
        self._engine.set_volume(volume)

    @Slot(str, float, int)
    def configure_smart_shuffle(self, mode: str, temperature: float,
                                recency_window_days: int) -> None:
        """Параметры умной рандомизации из конфига/настроек.

        Применяются к следующему построению очереди и к каждому новому
        проходу зацикленных случайных режимов (показы в UI не требуют
        перезапуска текущего трека). OFF сохраняет старое поведение.
        """
        self._smart_mode = mode if mode in SMART_MODES else SMART_OFF
        temp = float(temperature)
        self._smart_temperature = temp if temp > 0 and math.isfinite(temp) \
            else DEFAULT_TEMPERATURE
        window = int(recency_window_days)
        self._recency_window_days = window if window > 0 \
            else DEFAULT_RECENCY_WINDOW_DAYS

    def _fetch_scores(self, track_ids: list[int]) -> dict[int, TrackScore]:
        """История прослушиваний для треков очереди (по умолчанию — пустая)."""
        rows = self._db.get_connection().execute(
            'SELECT track_id, play_count, last_played_at FROM play_stats'
        ).fetchall()
        history = {
            row['track_id']: TrackScore(
                row['track_id'],
                max(0, int(row['play_count'] or 0)),
                row['last_played_at'],
            ) for row in rows
        }
        return {track_id: history.get(track_id, TrackScore(track_id))
                for track_id in track_ids}

    def _dynamic_order_factory(self, ids: Sequence[int]) -> list[int]:
        """Свежий порядок при каждом проходе/выборе случайной очереди.

        Режим, температура, окно и история читаются ЗДЕСЬ И СЕЙЧАС (а не
        снимком при создании очереди), поэтому переключатель в хедере и
        изменение температуры действуют на активный плейлист сразу.
        """
        track_ids = list(ids)
        history = self._fetch_scores(track_ids)
        return weighted_shuffle(
            track_ids, [history[tid] for tid in track_ids],
            self._smart_mode, self._smart_temperature,
            self._recency_window_days, self._rng)

    def _make_queue(self, track_ids: list[int],
                    order_mode: str) -> PlaybackQueue:
        """Очередь: для random-режимов — динамический взвешенный порядок.

        OFF тоже идёт через фабрику: weighted_shuffle в OFF-ветке делает
        обычный равномерный shuffle, так что переключение OFF <-> ON
        работает и на уже существующей очереди.
        """
        if order_mode not in _RANDOM_MODES:
            return PlaybackQueue(track_ids, order_mode, self._rng)
        return PlaybackQueue(track_ids, order_mode, self._rng,
                             self._dynamic_order_factory)

    @Slot(float)
    def seek(self, seconds: float) -> None:
        """Перемотка шкалой воспроизведения (passthrough в движок)."""
        self._engine.seek(seconds)

    @Slot(result=bool)
    def is_playing(self) -> bool:
        return self._engine.is_playing()

    @Slot()
    def toggle_play(self) -> None:
        """Кнопка play/pause в QML (только через контроллер)."""
        if self._engine.is_playing():
            self._engine.pause()
        elif self._engine.position_seconds() is not None:
            self._engine.resume()

    @Slot(result=str)
    def order_mode(self) -> str:
        """Текущий режим порядка (для кнопок shuffle/repeat)."""
        if self._queue is not None:
            return self._queue.order_mode
        if self._playlist_id is not None:
            row = self._db.get_connection().execute(
                'SELECT order_mode FROM playlists WHERE id = ?',
                (self._playlist_id,)).fetchone()
            if row is not None:
                return str(row['order_mode'])
        return ORDER_SEQUENTIAL

    @Slot(str, int)
    def set_order_mode(self, mode: str, playlist_id: int = -1) -> None:
        """Shuffle/repeat-кнопки: сменить режим плейлиста.

        Единственный источник правды: контроллер. ``playlist_id`` — целевой
        плейлист из UI (QML передаёт -1, если выбран не он). Режим пишется
        в БД только для целевого плейлиста; активная очередь меняется,
        только если это тот же плейлист, — так два плейлиста не расходятся.
        """
        if mode not in ORDER_MODES:
            return
        target = playlist_id if playlist_id > 0 else self._playlist_id
        if target is not None:
            with self._db.transaction() as tx:
                tx.execute('UPDATE playlists SET order_mode = ? WHERE id = ?',
                           (mode, target))
        if self._queue is not None and target == self._playlist_id:
            self._queue.set_order_mode(mode)

    @Slot(result=str)
    def prev_track_label(self) -> str:
        """Подпись предыдущего трека для колонки 3."""
        return self._label_for(self._queue.peek_prev()) \
            if self._queue is not None else ''

    @Slot(result=str)
    def next_track_label(self) -> str:
        """Подпись следующего трека для колонки 3."""
        return self._label_for(self._queue.peek_next()) \
            if self._queue is not None else ''

    @Slot(result=str)
    def current_track_details(self) -> str:
        """Исполнитель, расширение и частота текущего трека для колонки 3."""
        if self._current_track is None:
            return ''
        row = self._current_track
        parts = [row['artist'] or '—', str(row['format'] or '—').upper()]
        if row['sample_rate']:
            parts.append(f'{row["sample_rate"]} Гц')
        return ' · '.join(parts)

    def _label_for(self, track_id: int | None) -> str:
        if track_id is None:
            return '—'
        row = self._fetch_track_row(track_id)
        if row is None:
            return '—'
        label = row['title'] or row['path']
        if row['artist']:
            label = f'{label} — {row["artist"]}'
        if self._playlist_name:
            label = f'{label} ({self._playlist_name})'
        return label

    def shutdown(self) -> None:
        """Сброс статистики при выходе из приложения."""
        self._flush_current()
        self._stop_playback()
        self._poll_timer.stop()
        self.bridge.shutdown()  # больше не выгружаем события при выходе

    # --- внутреннее ---

    def _fetch_track_row(self, track_id: int) -> dict | None:
        """Строка tracks как dict или None, если id отсутствует."""
        conn = self._db.get_connection()
        row = conn.execute(
            'SELECT * FROM tracks WHERE id = ?', (track_id,)).fetchone()
        return dict(row) if row is not None else None

    def _fetch_playlist_info(self, playlist_id: int) -> PlaylistInfo | None:
        conn = self._db.get_connection()
        row = conn.execute(
            'SELECT id, name, order_mode FROM playlists WHERE id = ?',
            (playlist_id,)).fetchone()
        if row is None:
            return None
        return PlaylistInfo(row['id'], row['name'], row['order_mode'])

    def _flatten_playlist_from_db(self, playlist_id: int) -> list[int]:
        """Склейка плейлиста из БД; циклы/глубина -> перехват -> []."""
        conn = self._db.get_connection()
        playlists: dict[int, PlaylistInfo] = {}
        for row in conn.execute(
                'SELECT id, name, order_mode FROM playlists'):
            playlists[row['id']] = PlaylistInfo(
                row['id'], row['name'], row['order_mode'])
        items: dict[int, list[tuple[str, int]]] = {}
        for row in conn.execute(
                'SELECT playlist_id, ref_type, ref_id FROM playlist_items '
                'ORDER BY playlist_id, position'):
            items.setdefault(row['playlist_id'], []).append(
                (row['ref_type'], row['ref_id']))
        known_tracks = {row['id'] for row in
                        conn.execute('SELECT id FROM tracks')}
        try:
            return flatten_playlist(playlist_id, playlists, items,
                                    self._max_depth, known_tracks)
        except (PlaylistCycleError, PlaylistDepthError):
            return []

    def _switch_to_row(self, row: dict) -> None:
        """Сброс прошлого трека в статистику и старт нового по строке БД."""
        self._flush_current()
        self._start_row(row)

    def _start_track_id(self, track_id: int) -> None:
        row = self._fetch_track_row(track_id)
        if row is not None:
            self._start_row(row)

    def _start_row(self, row: dict) -> None:
        """Загрузка строки БД в движок; исключения декодирования наружу."""
        track = PlaybackTrack(
            path=Path(row['path']),
            title=row['title'],
            artist=row['artist'],
            volume=row['volume'],
            trim_start_sec=row['trim_start_sec'],
            trim_end_sec=row['trim_end_sec'],
            speed=row['speed'],
            pitch=row['pitch'],
            rate_mode=row['rate_mode'],
        )
        # Аккумулятор движка сбрасывается при play(): читаем ДО смены трека.
        self._engine.play(track)
        self._current_track = row
        self._stats_flushed = False
        self.currentTrackChanged.emit(self._current_track)

    def _advance(self) -> None:
        """Следующий трек очереди; пропускает битые/удалённые файлы.

        Ограничено len(очереди) попытками, чтобы битая последовательность
        не зациклила контроллер.
        """
        if self._queue is None:
            return
        for _ in range(max(1, len(self._queue))):
            track_id = self._queue.next()
            if track_id is None:
                self._stop_playback()
                return
            try:
                self._start_track_id(track_id)
            except (AudioEngineError, ValueError):
                continue  # битый файл: следующий
            return
        self._stop_playback()  # вся очередь битая

    def _on_track_finished(self, event: TrackFinished) -> None:
        """Слот EventBridge (GUI-поток): конец трека -> статистика -> далее."""
        current = self._current_track
        if current is None or Path(current['path']) != event.path:
            return  # устаревшее событие от предыдущего трека
        self._flush_current()
        if self._queue is None:
            self._stop_playback()  # одиночный трек доиграл — стоп
        else:
            self._advance()

    def _flush_current(self) -> None:
        """Сброс статистики текущего трека (одно upsert-предложение).

        Правило: played_seconds <= 0 -> трек не пишется вовсе
        (play_count не растёт). Аккумулятор нужно читать ДО переключения
        трека в движке — все вызовы это учитывают.
        """
        if self._current_track is None or self._stats_flushed:
            return
        played = self._engine.played_seconds()
        if played <= 0.0:
            return
        # идемпотентно: prev() в начале трека и т.п.
        self._stats_flushed = True
        with self._db.transaction() as tx:
            tx.execute(
                """
                INSERT INTO play_stats
                       (track_id, play_count, total_listened_sec,
                        last_played_at)
                VALUES (?, 1, ?,
                        strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                ON CONFLICT(track_id) DO UPDATE SET
                    play_count = play_count + 1,
                    total_listened_sec = total_listened_sec + ?,
                    last_played_at = excluded.last_played_at
                """,
                (self._current_track['id'], played, played),
            )

    def _stop_playback(self) -> None:
        """Остановить движок и очистить очередь (без сброса статистики)."""
        self._engine.stop()
        self._queue = None
        self._current_track = None
        self.currentTrackChanged.emit({})

    def _poll_position(self) -> None:
        """Таймер для QML: позиция в исходном времени, длительность трека."""
        if self._current_track is None:
            return
        position = self._engine.position_seconds()
        if position is None:
            return
        self.positionChanged.emit(position,
                                  float(self._current_track['duration_sec']))
