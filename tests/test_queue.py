"""Тесты чистой логики очереди (services/queue.py) — без Qt."""
import random

import pytest

from services.queue import (
    ORDER_RANDOM_LOOP_NO_REPEAT,
    ORDER_RANDOM_NO_REPEAT,
    ORDER_RANDOM_REPEAT,
    ORDER_SEQUENTIAL,
    ORDER_SEQUENTIAL_LOOP,
    PlaybackQueue,
    PlaylistCycleError,
    PlaylistDepthError,
    PlaylistInfo,
    flatten_playlist,
)

ALL_MODES = [
    ORDER_SEQUENTIAL,
    ORDER_SEQUENTIAL_LOOP,
    ORDER_RANDOM_NO_REPEAT,
    ORDER_RANDOM_REPEAT,
    ORDER_RANDOM_LOOP_NO_REPEAT,
]

# Простейшая фикстура-данные: плейлисты и их элементы.
PLAYLISTS = {
    1: PlaylistInfo(1, 'Root A', ORDER_SEQUENTIAL),
    2: PlaylistInfo(2, 'Nested B', ORDER_SEQUENTIAL),
    3: PlaylistInfo(3, 'Nested C', ORDER_SEQUENTIAL),
    4: PlaylistInfo(4, 'Nested D', ORDER_SEQUENTIAL),
}

ITEMS = {
    1: [('track', 10), ('playlist', 2), ('track', 11), ('playlist', 3)],
    2: [('track', 20)],
    3: [('track', 30), ('playlist', 4)],
    4: [('track', 40)],
}


def test_flatten_simple_order() -> None:
    assert flatten_playlist(1, PLAYLISTS, ITEMS, 5, {10, 11, 20, 30, 40}) == \
        [10, 20, 11, 30, 40]


def test_flatten_diamond_allowed() -> None:
    """Ромб A->B->D, A->C->D разрешён (D входит дважды, это не цикл)."""
    playlists = {1: PLAYLISTS[1], 2: PLAYLISTS[2], 3: PLAYLISTS[3],
                 4: PLAYLISTS[4]}
    items = {
        1: [('playlist', 2), ('playlist', 3)],
        2: [('playlist', 4)],
        3: [('playlist', 4)],
        4: [('track', 40)],
    }
    assert flatten_playlist(1, playlists, items, 5, {40}) == [40, 40]


def test_flatten_self_reference_rejected() -> None:
    items = {1: [('track', 10), ('playlist', 1)]}
    with pytest.raises(PlaylistCycleError):
        flatten_playlist(1, PLAYLISTS, items, 5, {10})


def test_flatten_mutual_cycle_rejected() -> None:
    """A->B->A отвергается (посещённые плейлисты текущей ветки)."""
    playlists = {1: PLAYLISTS[1], 2: PLAYLISTS[2]}
    items = {1: [('playlist', 2)], 2: [('playlist', 1)]}
    with pytest.raises(PlaylistCycleError):
        flatten_playlist(1, playlists, items, 5, set())


def test_flatten_depth_limit() -> None:
    # Цепочка 1->2->3->4 требует глубины 4; max_depth=3 -> ошибка.
    playlists = {i: PLAYLISTS[i] for i in (1, 2, 3, 4)}
    items = {1: [('playlist', 2)], 2: [('playlist', 3)],
             3: [('playlist', 4)], 4: [('track', 40)]}
    with pytest.raises(PlaylistDepthError):
        flatten_playlist(1, playlists, items, 3, {40})
    # Тот же граф с max_depth=4 проходит.
    assert flatten_playlist(1, playlists, items, 4, {40}) == [40]


def test_flatten_missing_refs_skipped() -> None:
    """Несуществующие треки и плейлисты пропускаются."""
    items = {1: [('track', 999), ('playlist', 777), ('track', 10)]}
    assert flatten_playlist(1, PLAYLISTS, items, 5, {10}) == [10]


def test_flatten_unknown_ref_type_skipped() -> None:
    items = {1: [('weird', 10), ('track', 10)]}
    assert flatten_playlist(1, PLAYLISTS, items, 5, {10}) == [10]


def test_flatten_missing_playlist_returns_empty() -> None:
    assert flatten_playlist(999, PLAYLISTS, ITEMS, 5, {10}) == []


def test_flatten_without_known_tracks_keeps_all() -> None:
    assert flatten_playlist(1, PLAYLISTS, ITEMS, 5, None) == \
        [10, 20, 11, 30, 40]


# --- PlaybackQueue: режимы порядка ---


def _make_queue(tracks: list[int], mode: str, seed: int) -> PlaybackQueue:
    return PlaybackQueue(tracks, mode, random.Random(seed))


def test_unknown_order_mode_rejected() -> None:
    with pytest.raises(ValueError):
        PlaybackQueue([1, 2], 'chaos', random.Random(0))


def test_two_queues_same_seed_identical_sequential() -> None:
    """Детерминизм: две очереди с одним seed дают одинаковые метрики."""

    def build(mode: str) -> list[int | None]:
        queue = _make_queue([10, 20, 30], mode, seed=42)
        return [queue.next(), queue.next(), queue.next(), queue.next(),
                queue.next()]

    assert build(ORDER_SEQUENTIAL) == [10, 20, 30, None, None]
    assert build(ORDER_SEQUENTIAL_LOOP) == [10, 20, 30, 10, 20]
    seq_a = build(ORDER_RANDOM_NO_REPEAT)
    seq_b = build(ORDER_RANDOM_NO_REPEAT)
    assert seq_a == seq_b
    repeat_a = _make_queue([10, 20, 30], ORDER_RANDOM_REPEAT, seed=7)
    repeat_b = _make_queue([10, 20, 30], ORDER_RANDOM_REPEAT, seed=7)
    assert [repeat_a.next() for _ in range(8)] == \
        [repeat_b.next() for _ in range(8)]


def test_sequential_mode() -> None:
    queue = _make_queue([10, 20, 30], ORDER_SEQUENTIAL, seed=1)
    assert [queue.next() for _ in range(4)] == [10, 20, 30, None]


def test_sequential_loop_mode() -> None:
    queue = _make_queue([10, 20, 30], ORDER_SEQUENTIAL_LOOP, seed=1)
    assert [queue.next() for _ in range(5)] == [10, 20, 30, 10, 20]


def test_random_no_repeat_is_permutation() -> None:
    queue = _make_queue([10, 20, 30, 40], ORDER_RANDOM_NO_REPEAT, seed=5)
    played = [queue.next() for _ in range(5)]
    assert sorted(played[:4]) == [10, 20, 30, 40]
    assert played[4] is None  # без повторов до конца


def test_random_repeat_can_repeat_and_is_infinite() -> None:
    queue = _make_queue([10, 20], ORDER_RANDOM_REPEAT, seed=3)
    played = [queue.next() for _ in range(50)]
    assert all(track in (10, 20) for track in played)
    assert len(set(played)) == 2  # повторы реально случаются


def test_random_loop_no_repeat_fresh_pass_each_cycle() -> None:
    queue = _make_queue([10, 20, 30], ORDER_RANDOM_LOOP_NO_REPEAT, seed=11)
    first_pass = [queue.next() for _ in range(3)]
    second_pass = [queue.next() for _ in range(3)]
    assert sorted(first_pass) == [10, 20, 30]
    assert sorted(second_pass) == [10, 20, 30]
    # Новый проход — свежая перестановка (не обязательно та же).
    third_pass = [queue.next() for _ in range(3)]
    assert sorted(third_pass) == [10, 20, 30]


def test_prev_sequential_none_at_start() -> None:
    queue = _make_queue([10, 20, 30], ORDER_SEQUENTIAL, seed=1)
    assert queue.prev() is None
    queue.next()
    queue.next()
    # prev() = шаг назад от текущего (20) -> предыдущий трек (10).
    assert queue.prev() == 10
    assert queue.current() == 10


def test_prev_loop_wraps() -> None:
    queue = _make_queue([10, 20, 30], ORDER_SEQUENTIAL_LOOP, seed=1)
    assert queue.prev() == 30  # с начала -> в конец (заворот)


def test_prev_random_repeat_uses_history() -> None:
    queue = _make_queue([10, 20, 30], ORDER_RANDOM_REPEAT, seed=9)
    first = queue.next()
    second = queue.next()
    assert queue.current() == second
    # prev() откатывает на трек, бывший до текущего, — round-trip next/prev.
    assert queue.prev() == first
    assert queue.current() == first
    assert queue.prev() is None  # история исчерпана


def test_current_tracks() -> None:
    queue = _make_queue([10, 20], ORDER_SEQUENTIAL, seed=1)
    assert queue.current() is None
    queue.next()
    assert queue.current() == 10
    queue.next()
    assert queue.current() == 20


def test_reset_restarts() -> None:
    queue = _make_queue([10, 20, 30], ORDER_SEQUENTIAL, seed=1)
    queue.next()
    queue.next()
    queue.reset()
    assert queue.next() == 10


# --- одноэлементная очередь и пустая ---


def test_single_track_all_modes() -> None:
    for mode in ALL_MODES:
        queue = _make_queue([10], mode, seed=13)
        assert queue.next() == 10, mode
        second = queue.next()
        if mode in (ORDER_SEQUENTIAL_LOOP, ORDER_RANDOM_REPEAT,
                    ORDER_RANDOM_LOOP_NO_REPEAT):
            assert second == 10, mode  # зацикленные играют бесконечно
        else:
            assert second is None, mode  # нецикличные заканчиваются


def test_empty_playlist_all_modes() -> None:
    for mode in ALL_MODES:
        queue = _make_queue([], mode, seed=13)
        assert queue.next() is None, mode
        assert queue.prev() is None, mode
        assert queue.current() is None, mode


def test_single_track_prev_all_modes() -> None:
    for mode in ALL_MODES:
        queue = _make_queue([10], mode, seed=13)
        queue.next()
        if mode == ORDER_RANDOM_REPEAT:
            # истории всего один элемент -> prev возвращает None
            assert queue.prev() is None, mode
        elif mode in (ORDER_SEQUENTIAL_LOOP, ORDER_RANDOM_LOOP_NO_REPEAT):
            assert queue.prev() == 10, mode  # заворот на себя
        else:
            assert queue.prev() is None, mode


def test_peek_does_not_mutate_queue() -> None:
    queue = _make_queue([10, 20, 30], ORDER_SEQUENTIAL, seed=1)
    assert queue.peek_next() == 10
    assert queue.peek_prev() is None

    assert queue.next() == 10
    assert queue.peek_next() == 20
    assert queue.peek_prev() is None  # нецикличная очередь: начала нет
    assert queue.current() == 10      # peek ничего не сдвинул

    looped = _make_queue([10, 20, 30], ORDER_SEQUENTIAL_LOOP, seed=1)
    looped.next()
    assert looped.peek_prev() == 30   # заворот в конец


def test_order_factory_used_for_random_modes() -> None:
    """Взвешенная фабрика ORDER задаёт порядок только в random-режимах."""
    factory = lambda ids: list(reversed(ids))

    random_queue = PlaybackQueue([10, 20, 30], ORDER_RANDOM_NO_REPEAT,
                                 random.Random(1), factory)
    assert [random_queue.next() for _ in range(3)] == [30, 20, 10]

    repeated = PlaybackQueue([10, 20, 30], ORDER_RANDOM_REPEAT,
                             random.Random(1), factory)
    assert repeated.next() == 30

    sequential = PlaybackQueue([10, 20, 30], ORDER_SEQUENTIAL,
                               random.Random(1), factory)
    assert [sequential.next() for _ in range(3)] == [10, 20, 30]


def test_set_order_mode_preserves_current() -> None:
    queue = _make_queue([10, 20, 30], ORDER_SEQUENTIAL, seed=1)
    queue.next()
    queue.next()
    assert queue.current() == 20

    queue.set_order_mode(ORDER_RANDOM_NO_REPEAT)
    assert queue.order_mode == ORDER_RANDOM_NO_REPEAT
    assert queue.current() == 20  # режим сменился без прыжка в начало

    with pytest.raises(ValueError):
        queue.set_order_mode('chaos')
