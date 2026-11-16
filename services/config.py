"""Загрузка и создание конфигурационного файла PyPlayer.

Конфиг хранится в data/config.json. При первом запуске (или при
повреждённом файле) создаётся с значениями по умолчанию; отсутствующие
ключи дозаполняются, некорректные значения известных ключей заменяются
дефолтными. Неизвестные ключи сохраняются как есть.
"""
import json
import math
import sys
from pathlib import Path
from typing import Any

# recency_window_days — окно затухания свежести прослушивания (дней) для
# умной рандомизации; отсутствующий ключ дозаполняется при загрузке (backfill).
DEFAULT_CONFIG: dict[str, Any] = {
    "music_dir": "music",
    "max_playlist_depth": 5,
    "smart_shuffle_default": "off",
    "shuffle_temperature": 0.5,
    "recency_window_days": 30,
}

_SHUFFLE_MODES = frozenset({"off", "direct", "inverse"})


def _read(path: Path) -> Any:
    """Читает JSON-файл; возвращает None, если файла нет или он повреждён.

    ValueError покрывает и json.JSONDecodeError, и UnicodeDecodeError
    (файл не в UTF-8, бинарный мусор и т.п.).
    """
    try:
        with path.open('r', encoding='utf-8') as fp:
            return json.load(fp)
    except (OSError, ValueError):
        return None


def _validate_key(key: str, value: Any) -> Any:
    """Валидирует один ключ конфига; некорректное значение -> дефолт."""
    default = DEFAULT_CONFIG.get(key)
    if key == 'music_dir':
        return value if isinstance(value, str) else default
    if key == 'max_playlist_depth':
        if isinstance(value, bool):
            return default
        return value if isinstance(value, int) and value >= 0 else default
    if key == 'smart_shuffle_default':
        # isinstance-защита обязательна: {} in frozenset(...) -> TypeError
        return value if isinstance(value, str) and value in _SHUFFLE_MODES \
            else default
    if key == 'shuffle_temperature':
        if isinstance(value, bool):
            return default
        # json.load принимает литералы Infinity/-Infinity/NaN — отвергаем их
        return float(value) if isinstance(value, (int, float)) \
            and math.isfinite(value) and value > 0 else default
    if key == 'recency_window_days':
        if isinstance(value, bool):
            return default
        return value if isinstance(value, int) and 1 <= value <= 3650 \
            else default
    return value  # неизвестные ключи сохраняются без изменений


def save_config(path: Path, config: dict[str, Any]) -> bool:
    """Сохраняет конфиг в ``path``; False при ошибке записи."""
    return _write(path, config)


def load_config(path: Path) -> dict[str, Any]:
    """Загружает конфиг из ``path``; при необходимости создаёт его.

    Если файла нет, он повреждён или не является словарём — записывает
    значения по умолчанию (неудача записи не фатальна: возвращается
    конфиг в памяти). Отсутствующие ключи дозаполняются, некорректные
    значения известных ключей заменяются дефолтными.
    """
    data = _read(path)
    config: dict[str, Any] = dict(DEFAULT_CONFIG)

    if isinstance(data, dict):
        for key, value in data.items():
            config[key] = _validate_key(key, value)

    if data != config:  # файла нет / он неполный или повреждён -> пересоздаём
        _write(path, config)
    return config


def _write(path: Path, config: dict[str, Any]) -> bool:
    """Записывает конфиг в ``path``; False при ошибке (путь занят и т.п.)."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('w', encoding='utf-8') as fp:
            json.dump(config, fp, ensure_ascii=False, indent=2)
            fp.write('\n')
    except OSError as exc:
        print(f'Не удалось записать {path}: {exc}', file=sys.stderr)
        return False
    return True
