"""Очередь воспроизведения: склейка рекурсивных плейлистов и 5 режимов
порядка. Чистая логика без Qt — тестируется напрямую.

Режимы (order_mode, значения совпадают с CHECK в схеме БД):

- ``sequential``           — треки по порядку один раз; в конце None (стоп).
- ``sequential_loop``      — по порядку с заворотом на начало.
- ``random_no_repeat``     — случайная перестановка; без повторов до
  конца, в конце None (стоп).
- ``random_repeat``        — случайный индекс на каждый next; повторы
  возможны; бесконечно.
- ``random_loop_no_repeat``— случайная перестановка; по исчерпании —
  свежая перестановка (новый проход); бесконечно.

Колонки ``loop`` и ``allow_repeats`` таблицы playlists остаются в схеме,
но поведение полностью определяет ``order_mode`` (зафиксированное
решение: 5 режимов); они считаются устаревшими и логикой не читаются.

Склейка (flatten): плейлист может ссылаться на треки и другие плейлисты
(ref_type 'track' | 'playlist'). Защита от циклов — по множеству
посещённых плейлистов ТЕКУЩЕЙ ветки (A->B->A отвергается, ромб
A->B->D, A->C->D разрешён). Глубина ограничена max_depth из конфига
(верхний плейлист = глубина 1). Ссылки на несуществующие треки и
плейлисты пропускаются.
"""
from __future__ import annotations

import random
from collections.abc import Callable, Container, Mapping, Sequence
from dataclasses import dataclass

ORDER_SEQUENTIAL = 'sequential'
ORDER_SEQUENTIAL_LOOP = 'sequential_loop'
ORDER_RANDOM_NO_REPEAT = 'random_no_repeat'
ORDER_RANDOM_REPEAT = 'random_repeat'
ORDER_RANDOM_LOOP_NO_REPEAT = 'random_loop_no_repeat'
ORDER_MODES = frozenset({
    ORDER_SEQUENTIAL,
    ORDER_SEQUENTIAL_LOOP,
    ORDER_RANDOM_NO_REPEAT,
    ORDER_RANDOM_REPEAT,
    ORDER_RANDOM_LOOP_NO_REPEAT,
})

_LOOP_MODES = frozenset({ORDER_SEQUENTIAL_LOOP, ORDER_RANDOM_LOOP_NO_REPEAT})
_SHUFFLE_MODES = frozenset({ORDER_RANDOM_NO_REPEAT,
                            ORDER_RANDOM_LOOP_NO_REPEAT})


class PlaylistCycleError(ValueError):
    """Плейлист содержит цикл (A->B->A или ссылку на себя)."""


class PlaylistDepthError(ValueError):
    """Глубина вложенности плейлистов превысила max_depth из конфига."""


@dataclass(frozen=True)
class PlaylistInfo:
    """Данные плейлиста, достаточные для склейки и очереди."""

    playlist_id: int
    name: str = ''
    order_mode: str = ORDER_SEQUENTIAL


def flatten_playlist(
    playlist_id: int,
    playlists: Mapping[int, PlaylistInfo],
    items: Mapping[int, Sequence[tuple[str, int]]],
    max_depth: int,
    known_tracks: Container[int] | None = None,
) -> list[int]:
    """Разворачивает плейлист в плоский список id треков.

    ``items``: {playlist_id: [(ref_type, ref_id), ...]} в порядке позиций.
    ``known_tracks``: если задан, ссылки на отсутствующие треки пропускаются
    (карточка могла быть удалена из библиотеки). Ссылки на отсутствующие
    плейлисты тоже пропускаются. Циклы и превышение глубины — ошибки.
    """
    if playlist_id not in playlists:
        return []
    # Рекурсивный _flatten не должен упереться в sys.getrecursionlimit()
    # раньше, чем в max_depth из конфига (глубокие цепочки плейлистов).
    max_depth = min(max_depth, 900)
    tracks: list[int] = []
    _flatten(playlist_id, playlists, items, max_depth,
             path=frozenset({playlist_id}), depth=1,
             tracks=tracks, known_tracks=known_tracks)
    return tracks


def _flatten(playlist_id: int,
             playlists: Mapping[int, PlaylistInfo],
             items: Mapping[int, Sequence[tuple[str, int]]],
             max_depth: int,
             path: frozenset[int],
             depth: int,
             tracks: list[int],
             known_tracks: Container[int] | None) -> None:
    if depth > max_depth:
        raise PlaylistDepthError(
            f'Глубина плейлиста {playlist_id} превысила max_depth='
            f'{max_depth}')
    for ref_type, ref_id in items.get(playlist_id, ()):
        if ref_type == 'track':
            if known_tracks is None or ref_id in known_tracks:
                tracks.append(ref_id)
        elif ref_type == 'playlist':
            if ref_id not in playlists:
                continue  # ссылка на удалённый плейлист
            if ref_id in path:
                raise PlaylistCycleError(
                    f'Цикл плейлистов: {sorted(path)} -> {ref_id}')
            _flatten(ref_id, playlists, items, max_depth,
                     path | {ref_id}, depth + 1, tracks, known_tracks)
        # иные ref_type пропускаем (схема CHECK допускает только два)


class PlaybackQueue:
    """Очередь треков с режимом порядка; seed инъецируется для тестов."""

    def __init__(self, track_ids: Sequence[int],
                 order_mode: str = ORDER_SEQUENTIAL,
                 rng: random.Random | None = None,
                 order_factory: Callable[[Sequence[int]],
                                         list[int]] | None = None) -> None:
        """``order_factory`` — опциональный взвешенный порядок для
        случайных режимов (умная рандомизация). OFF/SEQ — как раньше.
        """
        if order_mode not in ORDER_MODES:
            raise ValueError(
                f'Неизвестный режим порядка: {order_mode!r}')
        self._mode = order_mode
        self._rng = rng if rng is not None else random.Random()
        self._order_factory = order_factory
        self._tracks = list(track_ids)
        self._pos = -1
        self._history: list[int] = []  # для random_repeat (prev)
        self._current_id: int | None = None  # для current() во всех режимах
        self._prepare()

    def _shuffled(self, ids: Sequence[int]) -> list[int]:
        """Новая перестановка: взвешенная (если задана) или равномерная."""
        result = list(ids)
        if self._order_factory is not None:
            return self._order_factory(result)
        self._rng.shuffle(result)
        return result

    def _prepare(self) -> None:
        if self._mode in _SHUFFLE_MODES:
            self._seq = self._shuffled(self._tracks)
        else:
            self._seq = list(self._tracks)
        self._pos = -1
        self._history.clear()
        self._current_id = None

    def __len__(self) -> int:
        return len(self._tracks)

    @property
    def order_mode(self) -> str:
        return self._mode

    def current(self) -> int | None:
        """Текущий трек очереди или None (ещё не начали / конец).

        Единый источник для всех режимов: в random_repeat позиция в
        перестановке не используется, поэтому текущий id хранится отдельно.
        prev()/next() всегда согласованы с current() (round-trip работает).
        """
        return self._current_id

    def next(self) -> int | None:
        """Переход к следующему треку; None = конец (кроме зацикленных)."""
        n = len(self._seq)
        if n == 0:
            return None
        if self._mode == ORDER_RANDOM_REPEAT:
            if self._order_factory is not None:
                track_id = self._order_factory(list(self._tracks))[0]
            else:
                track_id = self._tracks[self._rng.randrange(n)]
            self._history.append(track_id)
            self._current_id = track_id
            return track_id
        self._pos += 1
        if self._pos >= n:
            if self._mode not in _LOOP_MODES:
                self._current_id = None  # конец очереди
                return None
            # Только random_loop_no_repeat начинает проход с НОВОЙ
            # перестановки. sequential_loop заворачивается на исходный
            # порядок — перемешивать его нельзя.
            if self._mode == ORDER_RANDOM_LOOP_NO_REPEAT:
                self._seq = self._shuffled(self._tracks)
            self._pos = 0
        self._current_id = self._seq[self._pos]
        return self._current_id

    def prev(self) -> int | None:
        """Возврат к ПРЕДЫДУЩЕМУ треку (откат на один шаг next()).

        None = предыдущего нет (начало нецикличной очереди или пустая
        история в random_repeat). Текущий трек при None не меняется.
        """
        n = len(self._seq)
        if n == 0:
            return None
        if self._mode == ORDER_RANDOM_REPEAT:
            # История = [.., предыдущий, текущий]. Убираем текущий и
            # возвращаем предыдущий — next()/prev() round-trip сходится.
            if len(self._history) < 2:
                return None
            self._history.pop()
            self._current_id = self._history[-1]
            return self._current_id
        if self._pos <= 0:
            if self._mode in _LOOP_MODES:
                self._pos = n - 1
            else:
                return None
        else:
            self._pos -= 1
        self._current_id = self._seq[self._pos]
        return self._current_id

    def peek_next(self) -> int | None:
        """Следующий трек для UI (не меняет состояние очереди).

        В random_repeat следующий трек заранее неизвестен — None.
        """
        if not self._seq or self._mode == ORDER_RANDOM_REPEAT:
            return None
        next_pos = self._pos + 1
        if next_pos >= len(self._seq):
            if self._mode not in _LOOP_MODES:
                return None
            next_pos = 0
        return self._seq[next_pos]

    def peek_prev(self) -> int | None:
        """Предыдущий трек для UI (не меняет состояние очереди)."""
        if not self._seq:
            return None
        if self._mode == ORDER_RANDOM_REPEAT:
            return self._history[-2] if len(self._history) >= 2 else None
        if self._pos <= 0:
            return self._seq[-1] if self._mode in _LOOP_MODES else None
        return self._seq[self._pos - 1]

    def set_order_mode(self, mode: str) -> None:
        """Сменить режим на лету (кнопки shuffle/repeat в UI).

        По возможности сохраняется текущий трек, чтобы смена режима не
        выбивала слушателя в начало очереди.
        """
        if mode not in ORDER_MODES:
            raise ValueError(f'Неизвестный режим порядка: {mode!r}')
        current = self._current_id
        self._mode = mode
        self._prepare()
        if current is not None and current in self._seq:
            self._pos = self._seq.index(current)
            self._current_id = current
            if mode == ORDER_RANDOM_REPEAT:
                self._history.append(current)

    def reset(self) -> None:
        """С начала (случайные режимы получают новую перестановку)."""
        self._prepare()
