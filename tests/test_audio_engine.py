"""Тесты аудио-движка (services/audio/engine.py) с FakeSink — CI-безопасно."""
import wave
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import QCoreApplication

from services.audio.engine import (
    AudioEngineError,
    EventBridge,
    FakeSink,
    PlaybackEngine,
    PlaybackTrack,
    TrackFinished,
)

_RATE = 8000


@pytest.fixture(scope='module')
def qapp() -> QCoreApplication:
    """QCoreApplication для QObject-логики (без GUI-платформы)."""
    return QCoreApplication.instance() or QCoreApplication([])


@pytest.fixture
def make_wav_file(tmp_path: Path) -> Callable[..., Path]:
    """Фабрика wav с ненулевым содержимым (пила в int16)."""

    def factory(name: str = 'a.wav', seconds: float = 1.0,
                rate: int = _RATE, channels: int = 1) -> Path:
        path = tmp_path / name
        count = int(seconds * rate) * channels
        samples = ((np.arange(count) % 5000) - 2500).astype('<i2')
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), 'wb') as wav:
            wav.setnchannels(channels)
            wav.setsampwidth(2)
            wav.setframerate(rate)
            wav.writeframes(samples.tobytes())
        return path

    return factory


@pytest.fixture
def make_engine() -> Callable[[], tuple[PlaybackEngine, FakeSink]]:
    """Движок с одним FakeSink; возвращает пару (engine, sink)."""
    created: list[PlaybackEngine] = []

    def factory() -> tuple[PlaybackEngine, FakeSink]:
        sink = FakeSink()
        engine = PlaybackEngine(sink_factory=lambda: sink)
        created.append(engine)
        return engine, sink

    yield factory
    for engine in created:
        engine.stop()


def _drain_finished(engine: PlaybackEngine) -> list[TrackFinished]:
    events = []
    while True:
        try:
            event = engine.event_queue.get_nowait()
        except Exception:
            break
        if isinstance(event, TrackFinished):
            events.append(event)
    return events


def test_plays_to_end_rate_1(make_engine, make_wav_file) -> None:
    wav = make_wav_file(seconds=1.0)
    engine, sink = make_engine()

    engine.play(PlaybackTrack(path=wav))
    sink.run_until_finished()

    assert sink.finished
    assert engine.played_seconds() == pytest.approx(1.0, abs=0.05)
    events = _drain_finished(engine)
    assert len(events) == 1
    assert events[0].path == wav


def test_plays_to_end_rate_2(make_engine, make_wav_file) -> None:
    """'both' mode, speed 2.0: аккумулятор ≈ duration / rate."""
    wav = make_wav_file(seconds=1.0)
    engine, sink = make_engine()

    engine.play(PlaybackTrack(path=wav, rate_mode='both', speed=2.0))
    sink.run_until_finished()

    assert sink.finished
    assert engine.played_seconds() == pytest.approx(0.5, abs=0.05)


def test_pitch_only_doubles_rate(make_engine, make_wav_file) -> None:
    """'pitch_only': наивный ресемплинг по pitch — длительность / pitch."""
    wav = make_wav_file(seconds=1.0)
    engine, sink = make_engine()

    engine.play(PlaybackTrack(path=wav, rate_mode='pitch_only', pitch=2.0))
    sink.run_until_finished()

    assert engine.played_seconds() == pytest.approx(0.5, abs=0.05)


def test_tempo_only_preserves_duration_scaling(make_engine,
                                               make_wav_file) -> None:
    """'tempo_only', speed 2.0: длительность выхода ≈ duration / speed."""
    wav = make_wav_file(seconds=1.0)
    engine, sink = make_engine()

    engine.play(PlaybackTrack(path=wav, rate_mode='tempo_only', speed=2.0))
    sink.run_until_finished()

    assert engine.played_seconds() == pytest.approx(0.5, abs=0.05)


def test_tempo_only_speed_1_is_lossless(make_engine, make_wav_file,
                                        tmp_path: Path) -> None:
    """speed=1.0 в 'tempo_only': окна OLA восстанавливают вход точно."""
    wav = make_wav_file(seconds=1.0)
    engine, sink = make_engine()

    engine.play(PlaybackTrack(path=wav, rate_mode='tempo_only', speed=1.0))
    sink.run_until_finished()

    with wave.open(str(wav), 'rb') as wav_file:
        original = np.frombuffer(wav_file.readframes(-1), dtype=np.int16)
    output = sink.collected_frames
    assert len(output) == len(original)
    assert int(np.max(np.abs(output.astype(np.int32) - original))) <= 1


def test_stereo_plays_to_end(make_engine, make_wav_file) -> None:
    wav = make_wav_file(seconds=0.5, channels=2)
    engine, sink = make_engine()

    engine.play(PlaybackTrack(path=wav))
    sink.run_until_finished()

    assert engine.played_seconds() == pytest.approx(0.5, abs=0.05)
    assert len(sink.collected_frames) == int(0.5 * _RATE) * 2


def test_trim_end_stops_and_emits_finished(make_engine,
                                           make_wav_file) -> None:
    wav = make_wav_file(seconds=1.0)
    engine, sink = make_engine()

    engine.play(PlaybackTrack(path=wav, trim_end_sec=0.4))
    sink.run_until_finished()

    assert sink.finished
    assert engine.played_seconds() == pytest.approx(0.4, abs=0.05)
    assert len(_drain_finished(engine)) == 1


def test_trim_start_skips_beginning(make_engine, make_wav_file) -> None:
    wav = make_wav_file(seconds=1.0)
    engine, sink = make_engine()

    engine.play(PlaybackTrack(path=wav, trim_start_sec=0.6))
    sink.run_until_finished()

    assert engine.played_seconds() == pytest.approx(0.4, abs=0.05)


def test_pause_freezes_accumulator_and_resumes(make_engine,
                                               make_wav_file) -> None:
    wav = make_wav_file(seconds=1.0)
    engine, sink = make_engine()

    engine.play(PlaybackTrack(path=wav))
    sink.run(0.3)
    assert engine.played_seconds() == pytest.approx(0.3, abs=0.05)

    engine.pause()
    sink.run(0.3)  # sink остановлен — ничего не тянется
    assert engine.played_seconds() == pytest.approx(0.3, abs=0.05)

    engine.resume()
    sink.run(0.2)
    assert engine.played_seconds() == pytest.approx(0.5, abs=0.05)


def test_volume_zero_mutes_output(make_engine, make_wav_file) -> None:
    wav = make_wav_file(seconds=0.5)
    engine, sink = make_engine()

    engine.play(PlaybackTrack(path=wav, volume=0.0))
    sink.run(0.2)

    output = sink.collected_frames
    assert len(output) > 0
    assert int(np.max(np.abs(output))) == 0


def test_volume_full_has_signal(make_engine, make_wav_file) -> None:
    wav = make_wav_file(seconds=0.5)
    engine, sink = make_engine()

    engine.play(PlaybackTrack(path=wav, volume=1.0))
    sink.run(0.2)

    assert int(np.max(np.abs(sink.collected_frames))) > 0


def test_volume_nan_is_ignored(make_engine, make_wav_file) -> None:
    wav = make_wav_file(seconds=0.5)
    engine, sink = make_engine()

    engine.play(PlaybackTrack(path=wav, volume=float('nan')))
    sink.run(0.2)
    # NaN-громкость -> дефолт 1.0, сигнал не заглушен, крэша нет
    assert int(np.max(np.abs(sink.collected_frames))) > 0

    engine.set_volume(float('nan'))  # игнорируется, громкость остаётся 1.0
    sink.run(0.1)
    assert int(np.max(np.abs(sink.collected_frames))) > 0


def test_seek_to_middle_accumulator_from_seek(make_engine,
                                              make_wav_file) -> None:
    """Аккумулятор копится непрерывно и не перескакивает на позицию seek
    (никакого «доматывания» середины трека — только реально сыгранные
    секунды до и после seek)."""
    wav = make_wav_file(seconds=1.0)
    engine, sink = make_engine()

    engine.play(PlaybackTrack(path=wav))
    sink.run(0.2)
    engine.seek(0.5)
    assert engine.position_seconds() == pytest.approx(0.5, abs=0.02)

    sink.run(0.2)
    assert engine.played_seconds() == pytest.approx(0.4, abs=0.05)
    assert engine.position_seconds() == pytest.approx(0.7, abs=0.05)


def test_seek_in_tempo_only_mode(make_engine, make_wav_file) -> None:
    wav = make_wav_file(seconds=1.0)
    engine, sink = make_engine()

    engine.play(PlaybackTrack(path=wav, rate_mode='tempo_only', speed=2.0))
    engine.seek(0.25)  # исходное время 0.25 c (вход), выходных прошло 0
    sink.run(0.2)      # 0.2 c выхода при speed 2 = 0.4 c входа
    assert engine.position_seconds() == pytest.approx(0.65, abs=0.05)
    assert engine.played_seconds() == pytest.approx(0.2, abs=0.05)


def test_seek_beyond_end_finishes(make_engine, make_wav_file) -> None:
    wav = make_wav_file(seconds=1.0)
    engine, sink = make_engine()

    engine.play(PlaybackTrack(path=wav))
    engine.seek(10.0)

    assert len(_drain_finished(engine)) == 1
    sink.run_until_finished()
    assert sink.finished


def test_rate_out_of_range_is_clamped(make_engine, make_wav_file) -> None:
    """speed 1000 -> зажим до 4.0; трек играется в 4 раза быстрее."""
    wav = make_wav_file(seconds=1.0)
    engine, sink = make_engine()

    engine.play(PlaybackTrack(path=wav, speed=1000.0))
    sink.run_until_finished()

    assert engine.played_seconds() == pytest.approx(0.25, abs=0.05)


def test_event_bridge_drains_queue(qapp: QCoreApplication, make_engine,
                                   make_wav_file) -> None:
    wav = make_wav_file(seconds=0.5)
    engine, sink = make_engine()
    engine.play(PlaybackTrack(path=wav))

    received: list[TrackFinished] = []
    bridge = EventBridge(engine)
    bridge.track_finished.connect(received.append)

    engine.event_queue.put(TrackFinished(path=wav))
    bridge.drain()
    assert len(received) == 1
    assert received[0].path == wav

    bridge.drain()  # очередь пуста — сигнала больше нет
    assert len(received) == 1


def test_fractional_budget_terminates(make_engine, make_wav_file) -> None:
    """Регрессия вечного спина: 8001 фрейм, tempo 2.0 — дробный бюджет.

    Бюджет ceil(8001/2) = 4001; после последнего неполного шага финиш
    обязан сработать, иначе _produce крутится с зажатой блокировкой.
    """
    wav = make_wav_file(seconds=8001 / _RATE)
    engine, sink = make_engine()

    engine.play(PlaybackTrack(path=wav, rate_mode='tempo_only', speed=2.0))
    sink.run_until_finished()

    assert sink.finished
    assert engine.played_seconds() == pytest.approx(0.5, abs=0.05)
    assert len(_drain_finished(engine)) == 1


def test_zero_length_track_finishes_immediately(make_engine,
                                                tmp_path: Path) -> None:
    """Регрессия StopIteration: пустой wav — генератор отдаёт пустой чанк."""
    path = tmp_path / 'empty.wav'
    with wave.open(str(path), 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(_RATE)
        wav.writeframes(b'')

    engine, sink = make_engine()
    engine.play(PlaybackTrack(path=path))
    sink.run(0.1)

    assert sink.finished
    assert len(_drain_finished(engine)) == 1


def test_trim_start_beyond_file_finishes_immediately(
        make_engine, make_wav_file) -> None:
    """trim_start за концом файла: вырожденное окно, без StopIteration."""
    wav = make_wav_file(seconds=1.0)
    engine, sink = make_engine()

    engine.play(PlaybackTrack(path=wav, trim_start_sec=2.0))
    sink.run(0.1)

    assert sink.finished
    assert len(_drain_finished(engine)) == 1


def test_play_missing_file_raises_engine_error(make_engine,
                                               tmp_path: Path) -> None:
    engine, _ = make_engine()

    with pytest.raises(AudioEngineError):
        engine.play(PlaybackTrack(path=tmp_path / 'missing.wav'))


def test_play_corrupt_file_raises_engine_error(make_engine,
                                               tmp_path: Path) -> None:
    path = tmp_path / 'broken.wav'
    path.write_bytes(b'\x00\x01\x02garbage not a wav')
    engine, _ = make_engine()

    with pytest.raises(AudioEngineError):
        engine.play(PlaybackTrack(path=path))


def test_is_playing_flags(make_engine, make_wav_file) -> None:
    wav = make_wav_file(seconds=1.0)
    engine, sink = make_engine()

    engine.play(PlaybackTrack(path=wav))
    assert engine.is_playing() is True

    engine.pause()
    assert engine.is_playing() is False

    engine.resume()
    assert engine.is_playing() is True

    engine.stop()
    assert engine.is_playing() is False


def test_seek_discards_stale_buffer(make_engine, make_wav_file) -> None:
    """После seek старый буфер не доигрывается: sink перезапущен на новой
    позиции, первые сэмплы после restart — содержимое с позиции seek."""
    wav = make_wav_file(seconds=1.0)
    engine, sink = make_engine()

    engine.play(PlaybackTrack(path=wav))
    sink.run(0.2)
    collected_before = len(sink.collected_frames)

    engine.seek(0.5)
    sink.run(0.2)

    fresh = sink.collected_frames[collected_before:]
    assert len(fresh) > 0
    with wave.open(str(wav), 'rb') as wav_file:
        original = np.frombuffer(wav_file.readframes(-1), dtype=np.int16)
    # свежий prime-чанк (128 фреймов) — это исходные сэмплы с 0.5 c
    expected = original[int(0.5 * _RATE):int(0.5 * _RATE) + len(fresh[:128])]
    assert np.array_equal(fresh[:len(expected)].astype(np.int32),
                          expected.astype(np.int32))
