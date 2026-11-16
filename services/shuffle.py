"""Умная рандомизация: взвешенный выбор без повторов (Efraimidis-Spirakis).

Формула весов для трека:

- ``listens = 1 + max(play_count, 0)`` — счётчик прослушиваний; «1»
  гарантирует положительный вес на холодном старте.
- ``recency = exp(-age_days / recency_window_days)`` — убывающая по
  времени функция свежести (0 для старых/непрослушанных, 1 для только что
  сыгранного). ``age_days`` считается от ``last_played_at`` до ``now``;
  невалидная/отсутствующая дата = «очень давно» (recency = 0).
- ``direct``  : ``raw = listens * (1 + recency)`` — ЧАЩЕ и ЧАЩЕ играли,
  тем выше шанс (диапазон [listens, 2*listens]).
- ``inverse`` : ``raw = (2 - recency) / listens`` — РЕЖЕ и ДАВНЕЕ играли,
  тем выше шанс (диапазон (0, 2]).
- Температура ``T`` — экспонента нормализации: ``weight = raw ** (1/T)``.
  ``T -> 0`` обостряет разницу (доминирует лучший), ``T -> inf``
  выравнивает веса (равномерный выбор). ``T`` должна быть > 0.

Холодный старт (все play_count = 0, истории нет): у всех ``raw`` одинаков,
значит все веса одинаковы — распределение равномерное.

Выбор без повторов: для каждого трека берём ``u = 1 - rng.random()``
(гарантированно 0 < u <= 1) и ключ ``key = u ** (1 / weight)``; сортируем
по убыванию ключа (ти-брейк по исходному индексу). Это корректная схема
Efraimidis-Spirakis: вероятность попадания в выборку пропорциональна весу.
Модуль чистый (без Qt/БД), seed инъецируется через ``random.Random``.
"""
from __future__ import annotations

import math
import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone

SMART_OFF = 'off'
SMART_DIRECT = 'direct'
SMART_INVERSE = 'inverse'
SMART_MODES = frozenset({SMART_OFF, SMART_DIRECT, SMART_INVERSE})

DEFAULT_TEMPERATURE = 0.5
DEFAULT_RECENCY_WINDOW_DAYS = 30
_SECONDS_PER_DAY = 86400.0


@dataclass(frozen=True)
class TrackScore:
    """История прослушиваний трека для расчёта веса."""

    track_id: int
    play_count: int = 0
    last_played_at: datetime | str | None = None


def _parse_time(value: datetime | str | None) -> datetime | None:
    """Парсит ISO-метку БД (…Z) в timezone-aware datetime."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            return None
    else:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _recency(score: TrackScore, now: datetime, window_days: int) -> float:
    played = _parse_time(score.last_played_at)
    if played is None:
        return 0.0
    age_days = max(0.0, (now - played).total_seconds() / _SECONDS_PER_DAY)
    return math.exp(-age_days / max(1, window_days))


def compute_weights(
    scores: Sequence[TrackScore],
    mode: str,
    temperature: float = DEFAULT_TEMPERATURE,
    recency_window_days: int = DEFAULT_RECENCY_WINDOW_DAYS,
    now: datetime | None = None,
) -> list[float]:
    """Веса треков в том же порядке, что и ``scores``.

    Некорректные входы (mode off/неизвестный, T <= 0, NaN) дают
    равномерные веса 1.0 — вызов всегда безопасен.
    """
    count = len(scores)
    if mode not in (SMART_DIRECT, SMART_INVERSE) or count == 0:
        return [1.0] * count
    temp = float(temperature)
    if not math.isfinite(temp) or temp <= 0:
        temp = DEFAULT_TEMPERATURE
    current = now or datetime.now(timezone.utc)
    exponent = 1.0 / temp
    weights: list[float] = []
    for score in scores:
        listens = 1.0 + max(0, int(score.play_count or 0))
        rec = _recency(score, current, int(recency_window_days))
        raw = listens * (1.0 + rec) if mode == SMART_DIRECT \
            else (2.0 - rec) / listens
        weight = raw ** exponent
        weights.append(weight if math.isfinite(weight) and weight > 0
                       else 0.0)
    if not any(weights):  # все нули — fall back на равномерные веса
        return [1.0] * count
    return weights


def weighted_order(
    track_ids: Sequence[int],
    weights: Sequence[float],
    rng: random.Random,
) -> list[int]:
    """Перестановка ``track_ids`` по весам (ES-сэмплинг без повторов).

    Отрицательные/нефинитные веса трактуются как 0; если все веса нулевые
    (или список пуст), возвращается равномерная перестановка.
    """
    ids = list(track_ids)
    if len(ids) <= 1:
        return ids
    clean: list[float] = []
    for weight in weights[:len(ids)]:
        try:
            value = float(weight)
        except (TypeError, ValueError):
            value = 0.0
        clean.append(value if math.isfinite(value) and value > 0 else 0.0)
    if len(clean) < len(ids):
        clean.extend([0.0] * (len(ids) - len(clean)))
    if not any(clean):
        rng.shuffle(ids)
        return ids

    keyed: list[tuple[float, int, int]] = []
    for index, weight in enumerate(clean):
        u = 1.0 - rng.random()          # (0, 1], без нуля
        try:
            key = u ** (1.0 / weight) if weight > 0 else 0.0
        except (OverflowError, ZeroDivisionError):
            key = 0.0
        if not math.isfinite(key):
            key = 0.0
        keyed.append((key, index, ids[index]))
    keyed.sort(key=lambda item: (-item[0], item[1]))
    return [item[2] for item in keyed]


def weighted_shuffle(
    track_ids: Sequence[int],
    scores: Sequence[TrackScore],
    mode: str,
    temperature: float = DEFAULT_TEMPERATURE,
    recency_window_days: int = DEFAULT_RECENCY_WINDOW_DAYS,
    rng: random.Random | None = None,
    now: datetime | None = None,
) -> list[int]:
    """Удобная обёртка: веса из истории + ES-перестановка.

    При ``mode == 'off'`` — обычная равномерная перестановка.
    """
    generator = rng if rng is not None else random.Random()
    ids = list(track_ids)
    if mode == SMART_OFF or len(ids) <= 1:
        generator.shuffle(ids)
        return ids
    weights = compute_weights(scores, mode, temperature,
                              recency_window_days, now)
    return weighted_order(ids, weights, generator)


def smart_order_factory(
    scores_by_id: dict[int, TrackScore],
    mode: str,
    temperature: float,
    recency_window_days: int,
    rng: random.Random,
    now: datetime | None = None,
) -> Callable[[Sequence[int]], list[int]]:
    """Фабрика порядков для PlaybackQueue (свежий проход = новая выборка)."""

    def factory(track_ids: Sequence[int]) -> list[int]:
        scores = [scores_by_id.get(track_id,
                                   TrackScore(track_id)) for track_id in
                  track_ids]
        return weighted_shuffle(track_ids, scores, mode, temperature,
                                recency_window_days, rng, now)

    return factory
