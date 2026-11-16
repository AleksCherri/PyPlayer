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

import random
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

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
    PlaylistCycleError,
    PlaylistDepthError,
    PlaybackQueue,
    PlaylistInfo,
    flatten_playlist,
)


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
        self._queue = PlaybackQueue(track_ids, info.order_mode, self._rng)
        self._playlist_id = playlist_id
        self._playlist_name = info.name
        self.playlistChanged.emit(playlist_id, info.name)
        self._advance()

    def pause(self) -> None:
        """Пауза (аккумулятор движка замирает — в статистику не пишем)."""
        self._engine.pause()

    def resume(self) -> None:
        """Продолжить после паузы."""
        self._engine.resume()

    def stop(self) -> None:
        """Остановка: текущий трек сбрасывается в статистику."""
        self._flush_current()
        self._stop_playback()

    def next_track(self) -> None:
        """Следующий трек по режиму порядка (None в конце — стоп)."""
        if self._queue is None:
            return
        self._flush_current()
        self._advance()

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

    def set_volume(self, volume: float) -> None:
        """Громкость 0..1 (passthrough в движок; NaN игнорируется им)."""
        self._engine.set_volume(volume)

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
        self._stats_flushed = True  # идемпотентно: prev() в начале трека и т.п.
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
