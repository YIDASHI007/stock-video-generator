from __future__ import annotations

import os

from stock_video_generator.desktop import (
    DEFAULT_NO_PROXY,
    _configure_proxy_environment,
)


def _clear_proxy_environment(monkeypatch) -> None:
    for name in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "no_proxy",
    ):
        monkeypatch.delenv(name, raising=False)


def test_configure_proxy_environment_bridges_windows_proxy(monkeypatch):
    _clear_proxy_environment(monkeypatch)
    monkeypatch.setattr(
        "stock_video_generator.desktop.urllib.request.getproxies",
        lambda: {
            "http": "http://127.0.0.1:17891",
            "https": "http://127.0.0.1:17891",
        },
    )

    _configure_proxy_environment({})

    assert os.environ["HTTP_PROXY"] == "http://127.0.0.1:17891"
    assert os.environ["HTTPS_PROXY"] == "http://127.0.0.1:17891"
    no_proxy = os.environ["NO_PROXY"].split(",")
    assert all(value in no_proxy for value in DEFAULT_NO_PROXY)


def test_configure_proxy_environment_can_be_disabled(monkeypatch):
    _clear_proxy_environment(monkeypatch)
    monkeypatch.setattr(
        "stock_video_generator.desktop.urllib.request.getproxies",
        lambda: {"https": "http://should-not-be-used:9999"},
    )

    _configure_proxy_environment({"use_system_proxy": False})

    assert "HTTPS_PROXY" not in os.environ
