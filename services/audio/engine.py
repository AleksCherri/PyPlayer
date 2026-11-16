"""Аудио-движок PyPlayer: потоковое воспроизведение декодированного PCM
через колбэки python-miniaudio.

Архитектура и потоки:

- **Поток устройства.** Sink (по умолчанию MiniaudioSink — реальное
  устройство) тянет PCM из генератора движка на своём потоке. Генератор
  получает через ``send(n)`` число необходимых фреймов и отдаёт s16-байты.
- **Поток GUI.** Общение через потокобезопасную очередь ``event_queue``:
  колбэк устройства кладёт туда события (только TrackFinished), их
  выгружает в GUI-потоке EventBridge (QObject + QTimer) и рассылает Qt
  сигналы. Из колбэка Qt-сигналы не испускаются никогда.
- **Состояние движка** (позиция в фреймах, хвост OLA, аккумулятор
  прослушанных секунд) живёт в движке и защищено блокировкой. Поэтому
  пауза = ``sink.stop()``, продолжение = перезапуск с новым генератором,
  читающим то же состояние, — без потери позиции и артефактов.
- **Бэкенд подменяем** (AudioSink): тесты используют FakeSink — он
  синхронно тянет PCM в управляющем потоке (CI-безопасно, без аудио-
  устройства) и запоминает выходные сэмплы.

Аккумулятор статистики ведётся только в памяти (для последующего flush в
БД при конце трека/выходе): считаются реально проигранные секунды
(выходные фреймы / частота выхода), паузы не учитываются.

DSP режимов (rate_mode):

- ``'both'`` — чистый вариспид: наивная линейная интерполяция с шагом
  ``speed``. Скорость воспроизведения и высота тона меняются вместе
  (x2.0 — и быстрее, и выше). Значение ``pitch`` игнорируется.
- ``'pitch_only'`` — наивный ресемплинг с шагом ``pitch``: тон сдвигается,
  длительность трека меняется обратно пропорционально (стандартное
  поведение этого режима). Значение ``speed`` игнорируется.
- ``'tempo_only'`` — время-растяжение OLA (overlap-add) с сохранением
  тона: зёрна по 4096 фреймов с 50% перекрытием (окно — треугольник),
  шаг анализа = шаг синтеза * speed. Зёрна проигрываются на родной
  скорости (тон не меняется), темп масштабируется на ``speed``.
  При speed = 1.0 reconstruction точная (треугольные окна в сумме дают
  единицу) — выход бит-в-бит совпадает с входом; при speed != 1 возможна
  лёгкая «размытость» транзиентов — осознанный компромисс лёгкости DSP.
"""
from __future__ import annotations

import math
import queue
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal

import miniaudio

_CHUNK_FRAMES = 1024
_PRIME_FRAMES = 128  # первый чанк генератора (маленький, чтобы seek не лагал)
_MIN_RATE = 0.25
_MAX_RATE = 4.0
_OLA_GRAIN = 4096          # длина зерна OLA, фреймов
_OLA_HOP = _OLA_GRAIN // 2  # шаг синтеза (50% перекрытие)


@dataclass
class PlaybackTrack:
    """Трек для воспроизведения: без привязки к БД (загружается из строки)."""

    path: Path
    title: str = ''
    artist: str = ''
    volume: float = 1.0            # 0..1
    trim_start_sec: float = 0.0
    trim_end_sec: float = 0.0      # 0.0 = играть до конца файла
    speed: float = 1.0
    pitch: float = 1.0
    rate_mode: str = 'both'        # 'tempo_only' | 'pitch_only' | 'both'


@dataclass(frozen=True)
class TrackFinished:
    """Событие: воспроизведение дошло до trim_end (или конца файла)."""

    path: Path


def _clamp_rate(value: float) -> float:
    """Ограничивает коэффициент скорости/тона диапазоном 0.25..4.0."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return 1.0
    if not math.isfinite(value):
        return 1.0
    return min(max(value, _MIN_RATE), _MAX_RATE)


def _sanitize_volume(value: float) -> float | None:
    """Валидирует громкость 0..1; None — значение невалидно (игнорировать)."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or value < 0.0:
        return None
    return min(value, 1.0)


class AudioSink(ABC):
    """Выход PCM: реальное устройство или тестовая заглушка.

    Генератор ``generator_factory()`` должен быть «запущен» (primed)
    до передачи в ``start``: первый ``next()`` отдаёт первый чанк,
    дальше через ``send(n_frames)``. Протокол совпадает с python-miniaudio.
    """

    @abstractmethod
    def start(self, generator_factory: Callable[[], Iterator[bytes]],
              sample_rate: int, channels: int) -> None:
        """Начать воспроизведение из нового генератора PCM."""

    @abstractmethod
    def stop(self) -> None:
        """Остановить воспроизведение (пауза; устройство остаётся открытым)."""


class MiniaudioSink(AudioSink):
    """Sink на реальном аудио-устройстве через python-miniaudio."""

    def __init__(self, buffersize_msec: int = 200) -> None:
        self._buffersize_msec = buffersize_msec
        self._device: miniaudio.PlaybackDevice | None = None

    def start(self, generator_factory: Callable[[], Iterator[bytes]],
              sample_rate: int, channels: int) -> None:
        self.stop()
        device = miniaudio.PlaybackDevice(
            output_format=miniaudio.SampleFormat.SIGNED16,
            nchannels=channels,
            sample_rate=sample_rate,
            buffersize_msec=self._buffersize_msec,
        )
        generator = generator_factory()
        next(generator)  # протокол: генератор запущен до device.start()
        device.start(generator)
        self._device = device

    def stop(self) -> None:
        if self._device is not None:
            self._device.stop()

    def close(self) -> None:
        if self._device is not None:
            self._device.close()
            self._device = None


class FakeSink(AudioSink):
    """Тестовый sink: синхронно тянет PCM и запоминает сэмплы (int16)."""

    def __init__(self, chunk_frames: int = _CHUNK_FRAMES) -> None:
        self.chunk_frames = chunk_frames
        self.sample_rate = 0
        self.channels = 0
        self.collected: list[np.ndarray] = []
        self.finished = False
        self._generator: Iterator[bytes] | None = None
        self._pending_chunk: bytes | None = None

    def start(self, generator_factory: Callable[[], Iterator[bytes]],
              sample_rate: int, channels: int) -> None:
        self.stop()
        self.sample_rate = sample_rate
        self.channels = channels
        self._generator = generator_factory()
        self._pending_chunk = next(self._generator)  # priming, как в miniaudio
        self.finished = False

    def stop(self) -> None:
        self._generator = None
        self._pending_chunk = None

    @property
    def collected_frames(self) -> np.ndarray:
        """Все собранные сэмплы одним массивом int16 (interleaved)."""
        if not self.collected:
            return np.zeros(0, dtype=np.int16)
        return np.concatenate(self.collected)

    def run(self, seconds: float) -> None:
        """Симулирует устройство: тянет ``seconds`` секунд выходного PCM."""
        if self._generator is None:
            return
        target = int(round(seconds * self.sample_rate))
        pulled = 0
        while pulled < target:
            chunk = self._pending_chunk
            if chunk is not None:
                self._pending_chunk = None
            else:
                try:
                    chunk = self._generator.send(
                        min(self.chunk_frames, target - pulled))
                except StopIteration:
                    self.finished = True
                    return
            frames = len(chunk) // (2 * self.channels)
            if frames == 0:  # конец данных
                self.finished = True
                return
            self.collected.append(
                np.frombuffer(chunk, dtype=np.int16).copy())
            pulled += frames

    def run_until_finished(self, max_seconds: float = 60.0) -> None:
        """Тянет PCM до конца трека (с предохранителем по времени)."""
        steps = int(max_seconds / 0.05)
        for _ in range(steps):
            if self._generator is None:
                break
            before = self.total_frames
            self.run(0.05)
            if self.finished:
                break
            if self.total_frames == before and self._generator is not None:
                continue  # генератор ещё жив, но не дал данных — ещё попытка
        self.finished = True

    @property
    def total_frames(self) -> int:
        if not self.collected:
            return 0
        return sum(len(chunk) for chunk in self.collected) // self.channels


class _TrackState:
    """Внутреннее состояние воспроизведения (защищено блокировкой движка)."""

    def __init__(self, track: PlaybackTrack, frames: np.ndarray,
                 sample_rate: int, channels: int) -> None:
        self.track = track
        self.frames = frames
        self.rate = sample_rate
        self.channels = channels
        duration_frames = len(frames) / channels
        self.trim_start = max(0.0, track.trim_start_sec) * sample_rate
        if 0.0 < track.trim_end_sec < duration_frames / sample_rate:
            self.trim_end = track.trim_end_sec * sample_rate
        else:
            self.trim_end = duration_frames
        self.src_pos = self.trim_start
        self.mode = track.rate_mode
        if self.mode == 'tempo_only':
            self.rate_factor = _clamp_rate(track.speed)   # темп OLA
        elif self.mode == 'pitch_only':
            self.rate_factor = _clamp_rate(track.pitch)   # шаг ресемплинга
        else:  # 'both' — чистый вариспид
            self.rate_factor = _clamp_rate(track.speed)
        self.volume = _sanitize_volume(track.volume)
        if self.volume is None:
            self.volume = 1.0
        self.played_seconds = 0.0
        self.finished = False
        # сколько выходных фреймов уже отдано sink'у (для position_seconds)
        self.output_pos = 0
        # сколько сгенерировано (бюджет OLA; может опережать отданное)
        self.output_generated = 0
        # состояние OLA / буфер невыходных фреймов
        # pre-roll: зерно начинается на полшага синтеза раньше окна, чтобы
        # первые фреймы выхода были полновесным crossfade (при speed = 1
        # выход бит-в-бит совпадает с входом с самого начала).
        self.ola_apos = self.trim_start - _OLA_HOP * self.rate_factor
        self.ola_primed = False
        self.ola_tail = np.zeros(_OLA_HOP * channels, dtype=np.float32)
        self.pending = np.zeros(0, dtype=np.float32)
        # бюджет выхода для OLA: длительность окна / темп
        self.output_target = (self.trim_end - self.trim_start) \
            / self.rate_factor

    def position_seconds(self) -> float:
        """Позиция в исходном времени трека (по отданным фреймам), сек."""
        src = self.trim_start + self.output_pos * self.rate_factor
        return src / self.rate


class PlaybackEngine:
    """Движок воспроизведения: трек -> PCM -> sink, события -> очередь."""

    def __init__(self, sink_factory: Callable[[], AudioSink] | None = None
                 ) -> None:
        self.event_queue: queue.Queue[TrackFinished] = queue.Queue()
        self._sink_factory = sink_factory or MiniaudioSink
        self._sink: AudioSink | None = None
        self._state: _TrackState | None = None
        self._lock = threading.Lock()

    # --- публичное API (поток GUI) ---

    def play(self, track: PlaybackTrack) -> None:
        """Загружает трек и начинает воспроизведение с начала."""
        self.stop()
        state = self._load_track(track)
        with self._lock:
            self._state = state
        sink = self._sink_factory()
        sink.start(lambda: self._generator(state), state.rate, state.channels)
        self._sink = sink

    def pause(self) -> None:
        """Пауза: устройство остановлено, позиция и аккумулятор заморожены."""
        if self._sink is not None:
            self._sink.stop()

    def resume(self) -> None:
        """Продолжить после паузы (новый генератор поверх того же состояния)."""
        with self._lock:
            state = self._state
            finished = state is None or state.finished
        if self._sink is None or finished or state is None:
            return
        self._sink.start(lambda: self._generator(state), state.rate,
                         state.channels)

    def stop(self) -> None:
        """Полная остановка и сброс состояния (аккумулятор обнуляется)."""
        if self._sink is not None:
            self._sink.stop()
            self._sink = None
        with self._lock:
            self._state = None

    def seek(self, seconds: float) -> None:
        """Переход на ``seconds`` (время исходного трека) внутри окна обрезки.

        Позиция и аккумулятор считаются по фактически отданным фреймам,
        поэтому seek не «доматывает» аккумулятор и не скачет из-за
        lookahead генератора.
        """
        with self._lock:
            state = self._state
            if state is None or state.finished:
                return
            src_target = max(0.0, float(seconds)) * state.rate
            src_target = min(max(src_target, state.trim_start),
                             state.trim_end)
            out_pos = (src_target - state.trim_start) / state.rate_factor
            state.pending = np.zeros(0, dtype=np.float32)
            state.output_pos = out_pos
            if state.mode == 'tempo_only':
                state.ola_apos = src_target - _OLA_HOP * state.rate_factor
                state.ola_tail = np.zeros(_OLA_HOP * state.channels,
                                          dtype=np.float32)
                state.ola_primed = False
                state.output_generated = out_pos
                end_reached = state.output_generated >= state.output_target
            else:
                state.src_pos = src_target
                end_reached = state.src_pos >= state.trim_end
            if end_reached:
                state.finished = True
                self.event_queue.put(TrackFinished(path=state.track.path))

    def set_volume(self, volume: float) -> None:
        """Громкость 0..1; NaN/некорректные значения игнорируются."""
        valid = _sanitize_volume(volume)
        with self._lock:
            if self._state is not None and valid is not None:
                self._state.volume = valid

    def played_seconds(self) -> float:
        """Реально прослушанные секунды (паузы исключены), только в памяти."""
        with self._lock:
            return self._state.played_seconds if self._state else 0.0

    def position_seconds(self) -> float | None:
        """Текущая позиция внутри окна обрезки (для UI), или None."""
        with self._lock:
            return self._state.position_seconds() if self._state else None

    def is_playing(self) -> bool:
        with self._lock:
            return self._state is not None and not self._state.finished \
                and self._sink is not None

    # --- внутреннее (поток устройства / генератор) ---

    def _load_track(self, track: PlaybackTrack) -> _TrackState:
        """Декодирует файл в PCM (s16, родные частота/каналы)."""
        path_str = str(track.path)
        info = miniaudio.get_file_info(path_str)
        decoded = miniaudio.decode_file(
            path_str,
            output_format=miniaudio.SampleFormat.SIGNED16,
            nchannels=info.nchannels,
            sample_rate=info.sample_rate,
        )
        frames = np.asarray(decoded.samples, dtype=np.int16).astype(np.float32)
        return _TrackState(track, frames, decoded.sample_rate,
                           decoded.nchannels)

    def _generator(self, state: _TrackState) -> Iterator[bytes]:
        """Генератор PCM для sink-потока: send(n_frames) -> s16-байты."""
        chunk = self._produce(state, _PRIME_FRAMES)
        while chunk:
            frames_needed = yield chunk
            if not isinstance(frames_needed, int) or frames_needed <= 0:
                frames_needed = _CHUNK_FRAMES
            chunk = self._produce(state, frames_needed)

    def _produce(self, state: _TrackState, n_frames: int) -> bytes:
        """Готовит до n_frames выходных фреймов (вызывается из sink-потока)."""
        with self._lock:
            if state is not self._state:
                return b''
            channels = state.channels
            need = max(1, int(n_frames))
            while len(state.pending) < need * channels and not state.finished:
                self._generate_into_pending(state, max(_CHUNK_FRAMES, need))
            take = min(need, len(state.pending) // channels) * channels
            if take == 0:
                return b''  # finished и буфер пуст — конец данных
            chunk = state.pending[:take]
            state.pending = state.pending[take:]
            state.played_seconds += (take // channels) / state.rate
            state.output_pos += take // channels
            gain = state.volume
            samples = np.clip(chunk * gain, -32768.0, 32767.0)
            return samples.astype(np.int16).tobytes()

    def _generate_into_pending(self, state: _TrackState, frames: int) -> None:
        """Дополняет state.pending обработанными (до громкости) фреймами."""
        if state.mode == 'tempo_only':
            self._generate_ola(state)
        else:
            self._generate_resample(state, frames)

    def _generate_resample(self, state: _TrackState, n_out: int) -> None:
        """Наивный ресемплинг (линейная интерполяция), шаг rate_factor."""
        step = state.rate_factor
        src = state.frames
        channels = state.channels
        remaining = n_out
        while remaining > 0 and not state.finished:
            idx = state.src_pos + step * np.arange(remaining, dtype=np.float64)
            over = np.nonzero(idx >= state.trim_end)[0]
            if len(over) > 0 and over[0] == 0:
                break  # позиция уже за trim_end
            if len(over) > 0:
                idx = idx[:over[0]]
            i0 = np.floor(idx).astype(np.int64)
            frac = (idx - i0).astype(np.float32)
            i1 = i0 + 1
            i0c = np.clip(i0 * channels, 0, len(src) - channels)
            i1c = np.clip(i1 * channels, 0, len(src) - channels)
            segment = np.empty(len(idx) * channels, dtype=np.float32)
            for channel in range(channels):
                lo = src[i0c + channel]
                hi = src[i1c + channel]
                segment[channel::channels] = lo + (hi - lo) * frac
            state.src_pos = float(idx[-1]) + step
            state.pending = np.concatenate((state.pending, segment))
            remaining -= len(idx)
            if len(over) > 0:
                # trim_end достигнут внутри этого куска
                state.finished = True
                self.event_queue.put(TrackFinished(path=state.track.path))
        if not state.finished and state.src_pos >= state.trim_end:
            state.finished = True
            self.event_queue.put(TrackFinished(path=state.track.path))

    def _generate_ola(self, state: _TrackState) -> None:
        """Одно зерно OLA: pitch-preserving time stretch (окно-треугольник).

        Первое зерно — pre-roll: только заполняет хвост crossfade, ничего
        не отдавая, чтобы начало выхода было полновесным. Выход строго
        ограничен бюджетом output_target (длительность окна / темп).
        """
        channels = state.channels
        if state.output_generated >= state.output_target:
            if not state.finished:
                state.finished = True
                self.event_queue.put(TrackFinished(path=state.track.path))
            return
        windowed = self._ola_windowed_grain(state, int(state.ola_apos))
        if not state.ola_primed:
            # pre-roll: хвост зерна пойдёт в crossfade следующего зерна
            state.ola_tail = windowed[_OLA_HOP * channels:].copy()
            state.ola_apos += _OLA_HOP * state.rate_factor
            state.ola_primed = True
            return
        # перекрытие с хвостом предыдущего зерна (crossfade)
        out = windowed[:_OLA_HOP * channels] \
            + state.ola_tail[:_OLA_HOP * channels]
        state.ola_tail = windowed[_OLA_HOP * channels:].copy()
        state.ola_apos += _OLA_HOP * state.rate_factor
        # не выходим за бюджет выхода (финиш может отрезать часть зерна)
        allowed_frames = min(_OLA_HOP,
                             int(state.output_target) - state.output_generated)
        state.pending = np.concatenate(
            (state.pending, out[:allowed_frames * channels]))
        state.output_generated += allowed_frames
        if state.output_generated >= state.output_target:
            state.finished = True
            self.event_queue.put(TrackFinished(path=state.track.path))

    @staticmethod
    def _ola_windowed_grain(state: _TrackState, i0: int) -> np.ndarray:
        """Зерно OLA: фреймы [i0, i0 + GRAIN) с нулевым дополнением, * окно."""
        channels = state.channels
        grain = np.zeros(_OLA_GRAIN * channels, dtype=np.float32)
        src_start = max(i0, 0) * channels          # i0 может быть < 0 (pre-roll)
        dst_start = max(0, -i0) * channels
        src_end = (i0 + _OLA_GRAIN) * channels
        take = max(0, min(len(state.frames), src_end) - src_start)
        if take > 0:
            grain[dst_start:dst_start + take] = \
                state.frames[src_start:src_start + take]
        return grain * _OLA_WINDOW.repeat(channels)


_OLA_WINDOW = np.concatenate([
    np.linspace(0.0, 1.0, _OLA_HOP, endpoint=False),
    np.linspace(1.0, 0.0, _OLA_HOP, endpoint=False),
]).astype(np.float32)


class EventBridge(QObject):
    """Выгружает очередь событий движка и рассылает Qt-сигналы (GUI-поток)."""

    track_finished = Signal(object)  # TrackFinished

    def __init__(self, engine: PlaybackEngine,
                 parent: QObject | None = None,
                 interval_ms: int = 100) -> None:
        super().__init__(parent)
        self._engine = engine
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self.drain)
        self._timer.start()

    def drain(self) -> None:
        """Выгружает очередь событий; вызывается только в GUI-потоке."""
        while True:
            try:
                event = self._engine.event_queue.get_nowait()
            except queue.Empty:
                break
            if isinstance(event, TrackFinished):
                self.track_finished.emit(event)
