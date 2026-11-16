"""Тесты загрузки/создания конфига (services/config.py)."""
import json
from pathlib import Path

from services.config import DEFAULT_CONFIG, load_config


def test_creates_file_with_defaults(tmp_path: Path) -> None:
    path = tmp_path / 'sub' / 'config.json'
    config = load_config(path)

    assert config == DEFAULT_CONFIG
    assert json.loads(path.read_text(encoding='utf-8')) == DEFAULT_CONFIG


def test_loads_existing_config(tmp_path: Path) -> None:
    path = tmp_path / 'config.json'
    custom = {"music_dir": "D:/музыка", "shuffle_temperature": 0.9}
    path.write_text(json.dumps(custom), encoding='utf-8')

    config = load_config(path)

    assert config["music_dir"] == "D:/музыка"
    assert config["shuffle_temperature"] == 0.9
    # отсутствующие ключи дозаполнены дефолтами
    assert config["max_playlist_depth"] == DEFAULT_CONFIG["max_playlist_depth"]


def test_corrupted_file_is_recreated(tmp_path: Path) -> None:
    path = tmp_path / 'config.json'
    path.write_text('{broken json', encoding='utf-8')

    config = load_config(path)

    assert config == DEFAULT_CONFIG
    assert json.loads(path.read_text(encoding='utf-8')) == DEFAULT_CONFIG


def test_non_dict_file_is_recreated(tmp_path: Path) -> None:
    path = tmp_path / 'config.json'
    path.write_text('[1, 2, 3]', encoding='utf-8')

    config = load_config(path)

    assert config == DEFAULT_CONFIG


def test_valid_file_is_not_rewritten(tmp_path: Path) -> None:
    path = tmp_path / 'config.json'
    path.write_text(json.dumps(DEFAULT_CONFIG), encoding='utf-8')
    before = path.read_text(encoding='utf-8')

    load_config(path)

    assert path.read_text(encoding='utf-8') == before
