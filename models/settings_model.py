"""Настройки плеера, доступные QML (переключатель умной рандомизации)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Property, Signal, Slot

from services.config import save_config

_SHUFFLE_MODES = frozenset({'off', 'direct', 'inverse'})


class SettingsModel(QObject):
    """Тонкая обёртка над config.json для UI."""

    smartShuffleChanged = Signal()
    temperatureChanged = Signal()

    def __init__(self, config_path: Path, config: dict[str, Any],
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._path = config_path
        self._config = dict(config)

    def _smart_shuffle(self) -> str:
        return str(self._config.get('smart_shuffle_default', 'off'))

    @Slot(str)
    def setSmartShuffle(self, mode: str) -> None:
        if mode not in _SHUFFLE_MODES or mode == self._smart_shuffle():
            return
        self._config['smart_shuffle_default'] = mode
        save_config(self._path, self._config)
        self.smartShuffleChanged.emit()

    smartShuffle = Property(str, _smart_shuffle, setSmartShuffle,
                            notify=smartShuffleChanged)

    def _temperature(self) -> float:
        return float(self._config.get('shuffle_temperature', 0.5))

    @Slot(float)
    def setTemperature(self, value: float) -> None:
        if value <= 0 or value == self._temperature():
            return
        self._config['shuffle_temperature'] = value
        save_config(self._path, self._config)
        self.temperatureChanged.emit()

    temperature = Property(float, _temperature, setTemperature,
                           notify=temperatureChanged)

    @Slot(result=int)
    def maxPlaylistDepth(self) -> int:
        return int(self._config.get('max_playlist_depth', 5))
