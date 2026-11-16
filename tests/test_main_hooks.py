"""Тесты немутающих хуков main.py: SIGINT и синхронизация настроек."""
import signal
from typing import Any

from main import install_signal_handlers, sync_smart_shuffle


class FakeApp:
    def __init__(self) -> None:
        self.quits = 0

    def quit(self) -> None:
        self.quits += 1


class FakePlayback:
    def __init__(self) -> None:
        self.shutdowns = 0
        self.configs: list[tuple[Any, ...]] = []

    def shutdown(self) -> None:
        self.shutdowns += 1

    def configure_smart_shuffle(self, *args: Any) -> None:
        self.configs.append(args)


class FakeSettings:
    smartShuffle = 'direct'
    temperature = 0.75

    def recencyWindowDays(self) -> int:
        return 45


def test_sigint_handler_flushes_stats_and_quits(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_signal(sig: int, handler: Any) -> None:
        captured['sig'] = sig
        captured['handler'] = handler

    monkeypatch.setattr(signal, 'signal', fake_signal)
    app = FakeApp()
    playback = FakePlayback()

    install_signal_handlers(app, playback)  # type: ignore[arg-type]

    assert captured['sig'] == signal.SIGINT
    captured['handler'](signal.SIGINT, None)
    assert playback.shutdowns == 1
    assert app.quits == 1


def test_sync_smart_shuffle_passes_all_settings() -> None:
    playback = FakePlayback()
    sync_smart_shuffle(playback, FakeSettings())  # type: ignore[arg-type]
    assert playback.configs == [('direct', 0.75, 45)]
