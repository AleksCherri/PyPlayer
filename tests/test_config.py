"""Тесты загрузки/создания конфига (services/config.py)."""
import json
from pathlib import Path

import pytest

from services.config import DEFAULT_CONFIG, load_config


def test_creates_file_with_defaults(tmp_path: Path) -> None:
    path = tmp_path / 'sub' / 'config.json'
    config = load_config(path)

    assert config == DEFAULT_CONFIG
    assert json.loads(path.read_text(encoding='utf-8')) == DEFAULT_CONFIG


def test_loads_existing_config_and_rewrites_file(tmp_path: Path) -> None:
    path = tmp_path / 'config.json'
    custom = {"music_dir": "D:/музыка", "shuffle_temperature": 0.9}
    path.write_text(json.dumps(custom), encoding='utf-8')

    config = load_config(path)

    assert config["music_dir"] == "D:/музыка"
    assert config["shuffle_temperature"] == 0.9
    # отсутствующие ключи дозаполнены дефолтами и файл перезаписан целиком
    on_disk = json.loads(path.read_text(encoding='utf-8'))
    assert set(on_disk) == set(DEFAULT_CONFIG)
    assert on_disk["max_playlist_depth"] == DEFAULT_CONFIG["max_playlist_depth"]
    assert on_disk["smart_shuffle_default"] == DEFAULT_CONFIG["smart_shuffle_default"]


def test_corrupted_file_is_recreated(tmp_path: Path) -> None:
    path = tmp_path / 'config.json'
    path.write_text('{broken json', encoding='utf-8')

    config = load_config(path)

    assert config == DEFAULT_CONFIG
    assert json.loads(path.read_text(encoding='utf-8')) == DEFAULT_CONFIG


def test_non_utf8_file_is_recreated(tmp_path: Path) -> None:
    path = tmp_path / 'config.json'
    path.write_bytes(b'\xff\xfe{bad')  # не UTF-8 -> UnicodeDecodeError

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


def test_invalid_values_fall_back_to_defaults(tmp_path: Path) -> None:
    path = tmp_path / 'config.json'
    bad = {
        "max_playlist_depth": "5",       # строка вместо int
        "shuffle_temperature": None,     # null
        "smart_shuffle_default": "yes",  # вне допустимых режимов
    }
    path.write_text(json.dumps(bad), encoding='utf-8')

    config = load_config(path)

    assert config["max_playlist_depth"] == DEFAULT_CONFIG["max_playlist_depth"]
    assert config["shuffle_temperature"] == DEFAULT_CONFIG["shuffle_temperature"]
    assert config["smart_shuffle_default"] == DEFAULT_CONFIG["smart_shuffle_default"]
    on_disk = json.loads(path.read_text(encoding='utf-8'))
    assert on_disk == config


def test_unknown_extra_keys_are_preserved(tmp_path: Path) -> None:
    path = tmp_path / 'config.json'
    data = dict(DEFAULT_CONFIG, custom_note="мои заметки")
    path.write_text(json.dumps(data), encoding='utf-8')

    config = load_config(path)

    assert config["custom_note"] == "мои заметки"
    on_disk = json.loads(path.read_text(encoding='utf-8'))
    assert on_disk["custom_note"] == "мои заметки"


def test_write_failure_returns_in_memory_defaults(
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / 'config.json'
    path.mkdir()  # путь занят каталогом -> запись невозможна

    config = load_config(path)

    assert config == DEFAULT_CONFIG
    assert 'Не удалось записать' in capsys.readouterr().err
