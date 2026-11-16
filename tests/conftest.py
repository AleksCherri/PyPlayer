"""Общие фикстуры тестов: БД в tmp-каталоге и генератор wav-файлов."""
from pathlib import Path

import pytest
import wave

from services.db import Database


@pytest.fixture
def db(tmp_path: Path):
    """База данных в tmp-каталоге; закрывается после теста."""
    database = Database(tmp_path / 'pyplayer.db')
    yield database
    database.close()


@pytest.fixture
def make_wav():
    """Фабрика коротких wav-файлов (моно, 16 бит)."""

    def factory(path: Path, duration: float = 0.5,
                rate: int = 8000) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), 'wb') as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(rate)
            wav.writeframes(b'\x00\x00' * int(rate * duration))
        return path

    return factory
