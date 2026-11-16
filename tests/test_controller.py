"""Тесты PlaybackController: статистика, очередь, signals, навигация."""
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication

from services.audio.engine import FakeSink, PlaybackEngine
from services.controller import PlaybackController
from services.db import Database
from services.queue import (
    ORDER_RANDOM_NO_REPEAT,
    ORDER_SEQUENTIAL,
    ORDER_SEQUENTIAL_LOOP,
    PlaybackQueue,
)
import random


@pytest.fixture(scope='module')
def qapp() -> QCoreApplication:
    return QCoreApplication.instance() or QCoreApplication([])


@pytest.fixture(autouse=True)
def _wavs_in_tmp(tmp_path: Path, monkeypatch) -> None:
    """Тестовые wav с относительными именами создаём в tmp-каталоге pytest."""
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def make_controller(db: Database):
    """Фабрика: (controller, engine, sink) с FakeSink и общим db."""
    created: list[tuple[PlaybackController, PlaybackEngine, FakeSink]] = []

    def factory(seed: int | None = None) -> tuple[
            PlaybackController, PlaybackEngine, FakeSink]:
        sink = FakeSink()
        engine = PlaybackEngine(sink_factory=lambda: sink)
        controller = PlaybackController(db, engine, max_depth=3, seed=seed)
        created.append((controller, engine, sink))
        return controller, engine, sink

    yield factory
    for controller, engine, _ in created:
        controller._poll_timer.stop()
        engine.stop()


def _add_track(db: Database, wav: Path, title: str,
               duration: float = 0.5) -> int:
    with db.transaction() as tx:
        cur = tx.execute(
            'INSERT INTO tracks (path, fingerprint, title, artist, '
            'duration_sec) VALUES (?, ?, ?, ?, ?)',
            (str(wav), f'fp-{title}', title, '', duration),
        )
    return int(cur.lastrowid)


def _add_playlist(db: Database, name: str, mode: str,
                  items: list[tuple[str, int]]) -> int:
    with db.transaction() as tx:
        cur = tx.execute(
            "INSERT INTO playlists (name, description, created_at, "
            "order_mode) VALUES (?, '', datetime('now'), ?)",
            (name, mode),
        )
        playlist_id = int(cur.lastrowid)
        for position, (ref_type, ref_id) in enumerate(items):
            tx.execute(
                'INSERT INTO playlist_items (playlist_id, position, '
                'ref_type, ref_id) VALUES (?, ?, ?, ?)',
                (playlist_id, position, ref_type, ref_id),
            )
    return playlist_id


def _stats(db: Database, track_id: int) -> dict | None:
    row = db.get_connection().execute(
        'SELECT play_count, total_listened_sec, last_played_at '
        'FROM play_stats WHERE track_id = ?', (track_id,)).fetchone()
    return dict(row) if row is not None else None


def _finish_active_track(engine: PlaybackEngine, sink: FakeSink,
                         controller: PlaybackController) -> None:
    """Доиграть трек и доставить TrackFinished в GUI-поток через мост."""
    sink.run_until_finished()
    controller.bridge.drain()
    assert engine.event_queue.empty()


def test_stats_flush_on_finish_only_once(
        qapp, db: Database, make_controller, make_wav) -> None:
    wav = make_wav(Path('a.wav'), duration=0.5)
    track_id = _add_track(db, wav, 'A')
    controller, engine, sink = make_controller()

    controller.play(track_id)
    sink.run(0.2)
    assert _stats(db, track_id) is None  # во время игры в БД не пишем

    _finish_active_track(engine, sink, controller)
    row = _stats(db, track_id)
    assert row is not None
    assert row['play_count'] == 1
    assert row['total_listened_sec'] == pytest.approx(0.5, abs=0.05)
    assert row['last_played_at'] is not None

    controller.bridge.drain()  # повторная выгрузка ничего не меняет
    controller.stop()
    assert _stats(db, track_id)['play_count'] == 1


def test_zero_played_seconds_not_counted(
        qapp, db: Database, make_controller, make_wav, monkeypatch) -> None:
    first = _add_track(db, make_wav(Path('first.wav')), 'First')
    second = _add_track(db, make_wav(Path('second.wav')), 'Second')
    controller, engine, _ = make_controller()

    controller.play(first)
    monkeypatch.setattr(engine, 'played_seconds', lambda: 0.0)
    controller.play(second)  # мгновенное переключение: 0 прослушанных секунд

    assert _stats(db, first) is None
    assert controller.current_track['id'] == second


def test_sequential_playlist_advances_and_stops(
        qapp, db: Database, make_controller, make_wav) -> None:
    t1 = _add_track(db, make_wav(Path('t1.wav')), 'T1')
    t2 = _add_track(db, make_wav(Path('t2.wav')), 'T2')
    playlist_id = _add_playlist(db, 'Seq', ORDER_SEQUENTIAL,
                                [('track', t1), ('track', t2)])
    controller, engine, sink = make_controller(seed=1)

    controller.play_playlist(playlist_id)
    assert controller.current_track['id'] == t1
    _finish_active_track(engine, sink, controller)
    assert controller.current_track['id'] == t2
    assert _stats(db, t1)['play_count'] == 1

    _finish_active_track(engine, sink, controller)
    assert controller.current_track is None  # конец нецикличной очереди
    assert _stats(db, t2)['play_count'] == 1


def test_sequential_loop_playlist_wraps(
        qapp, db: Database, make_controller, make_wav) -> None:
    track_id = _add_track(db, make_wav(Path('one.wav')), 'One')
    playlist_id = _add_playlist(db, 'Loop', ORDER_SEQUENTIAL_LOOP,
                                [('track', track_id)])
    controller, engine, sink = make_controller()

    controller.play_playlist(playlist_id)
    for _ in range(3):
        _finish_active_track(engine, sink, controller)
        assert controller.current_track['id'] == track_id
    assert _stats(db, track_id)['play_count'] == 3


def test_empty_and_cyclic_playlist_no_crash(
        qapp, db: Database, make_controller) -> None:
    controller, _, _ = make_controller()
    empty = _add_playlist(db, 'Empty', ORDER_SEQUENTIAL, [])
    controller.play_playlist(empty)
    assert controller.current_track is None

    b = _add_playlist(db, 'B', ORDER_SEQUENTIAL, [])
    a = _add_playlist(db, 'A', ORDER_SEQUENTIAL, [('playlist', b)])
    # A -> B -> A (цикл); добавляем ссылку из B в A после создания A.
    with db.transaction() as tx:
        tx.execute(
            'INSERT INTO playlist_items (playlist_id, position, ref_type, '
            'ref_id) VALUES (?, 0, ?, ?)', (b, 'playlist', a))
    controller.play_playlist(a)
    assert controller.current_track is None


def test_missing_track_ref_skipped(
        qapp, db: Database, make_controller, make_wav) -> None:
    track_id = _add_track(db, make_wav(Path('ok.wav')), 'Ok')
    playlist_id = _add_playlist(db, 'WithMissing', ORDER_SEQUENTIAL,
                                [('track', 99999), ('track', track_id)])
    controller, _, _ = make_controller()

    controller.play_playlist(playlist_id)
    assert controller.current_track['id'] == track_id


def test_next_prev_navigation(
        qapp, db: Database, make_controller, make_wav) -> None:
    ids = [
        _add_track(db, make_wav(Path(f'n{i}.wav')), f'N{i}')
        for i in range(3)
    ]
    playlist_id = _add_playlist(db, 'Nav', ORDER_SEQUENTIAL,
                                [('track', tid) for tid in ids])
    controller, _, _ = make_controller()
    heard: list[int] = []
    controller.currentTrackChanged.connect(
        lambda card: heard.append(card['id']) if card else None)

    controller.play_playlist(playlist_id)
    assert heard == [ids[0]]
    controller.next_track()
    controller.next_track()
    assert controller.current_track['id'] == ids[2]
    controller.prev_track()
    assert controller.current_track['id'] == ids[1]
    assert heard == [ids[0], ids[1], ids[2], ids[1]]


def test_random_playlist_respects_seed(
        qapp, db: Database, make_controller, make_wav) -> None:
    ids = [
        _add_track(db, make_wav(Path(f'r{i}.wav')), f'R{i}')
        for i in range(4)
    ]
    playlist_id = _add_playlist(db, 'Random', ORDER_RANDOM_NO_REPEAT,
                                [('track', tid) for tid in ids])
    expected = _make_expected_order(ids, seed=42)
    controller, engine, sink = make_controller(seed=42)
    heard: list[int] = []
    controller.currentTrackChanged.connect(
        lambda card: heard.append(card['id']) if card else None)

    controller.play_playlist(playlist_id)
    for _ in range(len(ids)):
        _finish_active_track(engine, sink, controller)
    assert heard == expected


def _make_expected_order(ids: list[int], seed: int) -> list[int]:
    queue = PlaybackQueue(ids, ORDER_RANDOM_NO_REPEAT, random.Random(seed))
    order = []
    while (track_id := queue.next()) is not None:
        order.append(track_id)
    return order


def test_position_changed_emitted(
        qapp, db: Database, make_controller, make_wav) -> None:
    track_id = _add_track(db, make_wav(Path('pos.wav'), duration=0.5),
                          'Pos', duration=0.5)
    controller, _, sink = make_controller()
    seen: list[tuple[float, float]] = []
    controller.positionChanged.connect(
        lambda position, duration: seen.append((position, duration)))

    controller.play(track_id)
    sink.run(0.2)
    controller._poll_position()

    assert len(seen) == 1
    assert seen[0][0] == pytest.approx(0.2, abs=0.03)
    assert seen[0][1] == pytest.approx(0.5)


def test_shutdown_flushes(
        qapp, db: Database, make_controller, make_wav) -> None:
    track_id = _add_track(db, make_wav(Path('quit.wav')), 'Quit')
    controller, _, sink = make_controller()

    controller.play(track_id)
    sink.run(0.2)
    controller.shutdown()

    assert _stats(db, track_id)['total_listened_sec'] == \
        pytest.approx(0.2, abs=0.03)
    assert controller.current_track is None


def test_volume_passthrough(
        qapp, db: Database, make_controller, make_wav) -> None:
    controller, engine, _ = make_controller()
    calls: list[float] = []
    engine.set_volume = calls.append  # type: ignore[method-assign]
    controller.set_volume(0.35)
    assert calls == [0.35]


def test_play_missing_track_raises(qapp, db: Database, make_controller) -> None:
    controller, _, _ = make_controller()
    with pytest.raises(ValueError):
        controller.play(99999)


def test_single_track_play_clears_queue(
        qapp, db: Database, make_controller, make_wav) -> None:
    first = _add_track(db, make_wav(Path('q1.wav')), 'Q1')
    second = _add_track(db, make_wav(Path('q2.wav')), 'Q2')
    playlist_id = _add_playlist(db, 'Q', ORDER_SEQUENTIAL,
                                [('track', first), ('track', second)])
    controller, engine, sink = make_controller()

    controller.play_playlist(playlist_id)
    controller.play(second)  # слушатель переключился на одиночный трек
    _finish_active_track(engine, sink, controller)

    assert controller.current_track is None  # не прыгнуло обратно в плейлист
