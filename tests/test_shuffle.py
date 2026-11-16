"""Тесты умной рандомизации (services/shuffle.py) — чистая логика."""
import math
import random
from datetime import datetime, timedelta, timezone

import pytest

from services.shuffle import (
    SMART_DIRECT,
    SMART_INVERSE,
    SMART_OFF,
    TrackScore,
    compute_weights,
    smart_order_factory,
    weighted_order,
    weighted_shuffle,
)

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _scores() -> list[TrackScore]:
    return [
        TrackScore(10, 50, NOW),                              # heavy, recent
        TrackScore(20, 0, NOW - timedelta(days=100)),         # light, old
    ]


def test_empty_and_single() -> None:
    assert weighted_order([], [], random.Random(1)) == []
    assert weighted_shuffle([], [], SMART_DIRECT,
                            rng=random.Random(1)) == []
    assert weighted_shuffle([7], [TrackScore(7)], SMART_INVERSE,
                            rng=random.Random(1)) == [7]


def test_weighted_order_preserves_multiset_and_is_deterministic() -> None:
    ids = [1, 2, 3, 4, 5]
    weights = [0.1, 5.0, 1.0, 2.0, 0.5]
    first = weighted_order(ids, weights, random.Random(42))
    second = weighted_order(ids, weights, random.Random(42))
    assert first == second
    assert sorted(first) == ids


def test_negative_nan_and_zero_weights_treated_as_zero() -> None:
    ids = [1, 2, 3]
    weights = [-1.0, float('nan'), 0.0]
    result = weighted_order(ids, weights, random.Random(7))
    assert sorted(result) == ids  # все нули -> равномерная перестановка

    mixed = weighted_order(ids, [-5.0, 4.0, 3.0], random.Random(7))
    assert sorted(mixed) == ids


def test_cold_start_weights_uniform() -> None:
    scores = [TrackScore(1), TrackScore(2), TrackScore(3)]
    for mode in (SMART_DIRECT, SMART_INVERSE, SMART_OFF):
        weights = compute_weights(scores, mode, now=NOW)
        assert len(set(weights)) == 1  # одинаковые => равномерный выбор
        assert all(weight > 0 for weight in weights)


def test_off_mode_is_uniform_permutation() -> None:
    ids = [1, 2, 3, 4]
    result = weighted_shuffle(ids, [TrackScore(i) for i in ids],
                              SMART_OFF, rng=random.Random(3))
    expected = list(ids)
    random.Random(3).shuffle(expected)
    assert result == expected  # OFF — обычный rng.shuffle, без весов


def test_skew_direct_more_than_uniform_more_than_inverse() -> None:
    """Много seeds: тяжёлый трек чаще первого в direct, чем в off/inverse."""
    def first_heavy_count(mode: str) -> int:
        count = 0
        for seed in range(200):
            order = weighted_shuffle([10, 20], _scores(), mode,
                                     temperature=0.5, rng=random.Random(seed),
                                     now=NOW)
            count += order[0] == 10
        return count

    direct = first_heavy_count(SMART_DIRECT)
    uniform = first_heavy_count(SMART_OFF)
    inverse = first_heavy_count(SMART_INVERSE)
    assert direct > uniform > inverse
    assert direct >= 190   # почти всегда
    assert inverse <= 10   # почти никогда


def test_temperature_flattens_and_sharpens() -> None:
    # Одинаковый play_count, разная свежесть: разница весов только в recency.
    scores = [
        TrackScore(1, 0, NOW),
        TrackScore(2, 0, NOW - timedelta(days=90)),
    ]

    def recent_first_count(temperature: float) -> int:
        return sum(
            weighted_shuffle([1, 2], scores, SMART_DIRECT,
                             temperature=temperature,
                             rng=random.Random(seed), now=NOW)[0] == 1
            for seed in range(200)
        )

    sharp = recent_first_count(0.1)
    flat = recent_first_count(3.0)
    assert sharp > flat
    assert flat > 100  # при высокой температуре близко к равномерному
    assert sharp >= 190


def test_recency_direction_direct_vs_inverse() -> None:
    scores = [
        TrackScore(1, 0, NOW),                       # свежий
        TrackScore(2, 0, NOW - timedelta(days=365)),  # старый
    ]
    direct = compute_weights(scores, SMART_DIRECT, now=NOW)
    inverse = compute_weights(scores, SMART_INVERSE, now=NOW)
    assert direct[0] > direct[1]     # direct любит свежее
    assert inverse[0] < inverse[1]   # inverse любит старое

    # Без истории у всех одинаково (холодный старт).
    no_history = [TrackScore(1), TrackScore(2)]
    assert compute_weights(no_history, SMART_DIRECT, now=NOW) == [1.0, 1.0]


def test_invalid_mode_temperature_and_window_are_safe() -> None:
    scores = _scores()
    assert compute_weights(scores, 'unknown', now=NOW) == [1.0, 1.0]
    assert compute_weights(scores, SMART_DIRECT, temperature=float('nan'),
                           now=NOW) == \
        compute_weights(scores, SMART_DIRECT, temperature=0.5, now=NOW)
    # Отрицательное окно не ломает exp (защита max(1, window)).
    weights = compute_weights(scores, SMART_INVERSE,
                              recency_window_days=-5, now=NOW)
    assert all(math.isfinite(weight) and weight > 0 for weight in weights)


def test_smart_order_factory_returns_new_permutation_each_call() -> None:
    scores = {score.track_id: score for score in _scores()}
    factory = smart_order_factory(scores, SMART_DIRECT, 0.5, 30,
                                  random.Random(11), NOW)
    first = factory([10, 20])
    second = factory([10, 20])
    assert sorted(first) == [10, 20]
    assert sorted(second) == [10, 20]
    assert first == second == [10, 20]  # direct: тяжёлый стабильно первый
