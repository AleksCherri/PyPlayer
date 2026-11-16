"""Загрузка и создание конфигурационного файла PyPlayer.

Конфиг хранится в data/config.json. При первом запуске (или при
повреждённом файле) создаётся с значениями по умолчанию; отсутствующие
ключи дозаполняются значениями по умолчанию.
"""
import json
from pathlib import Path
from typing import Any

DEFAULT_CONFIG: dict[str, Any] = {
    "music_dir": "music",
    "max_playlist_depth": 5,
    "smart_shuffle_default": "off",
    "shuffle_temperature": 0.5,
}


def _read(path: Path) -> Any:
    """Читает JSON-файл; возвращает None, если файла нет или он повреждён."""
    try:
        with path.open('r', encoding='utf-8') as fp:
            return json.load(fp)
    except (OSError, json.JSONDecodeError):
        return None


def load_config(path: Path) -> dict[str, Any]:
    """Загружает конфиг из ``path``; при необходимости создаёт его.

    Если файла нет, он повреждён или не является словарём — записывает
    значения по умолчанию. Отсутствующие ключи дозаполняются.
    """
    data = _read(path)
    config = dict(DEFAULT_CONFIG)

    if isinstance(data, dict):
        for key, value in data.items():
            config[key] = value
        if all(config[key] == data.get(key) for key in DEFAULT_CONFIG):
            return config  # файл полный и корректный — не перезаписываем

    _write(path, config)
    return config


def _write(path: Path, config: dict[str, Any]) -> None:
    """Записывает конфиг в ``path``, создавая родительский каталог."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8') as fp:
        json.dump(config, fp, ensure_ascii=False, indent=2)
        fp.write('\n')
