"""The optional background track: served from an env-configurable path, 404 when
absent (so dev / CI / fresh clones run without the 19 MB asset)."""
from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from nethackers.hub.api import create_app
from nethackers.hub.auth import LocalStubAuth
from nethackers.hub.store import Store


def _app(tmp_path: Any) -> TestClient:
    store = Store(tmp_path / "h.db")
    store.init_schema()
    return TestClient(create_app(store, LocalStubAuth({"t": "sam"}), git_factory=lambda tok: None))


def test_dictionary_audio_404_when_absent(tmp_path: Any, monkeypatch) -> None:
    monkeypatch.setenv("NETHACKERS_DICT_AUDIO", str(tmp_path / "nope.mp3"))
    client = _app(tmp_path)
    assert client.get("/dictionary.mp3").status_code == 404


def test_dictionary_audio_served_when_present(tmp_path: Any, monkeypatch) -> None:
    mp3 = tmp_path / "d.mp3"
    mp3.write_bytes(b"ID3\x03\x00\x00\x00" + b"\x00" * 64)
    monkeypatch.setenv("NETHACKERS_DICT_AUDIO", str(mp3))
    r = _app(tmp_path).get("/dictionary.mp3")
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/mpeg"
    assert r.content.startswith(b"ID3")
