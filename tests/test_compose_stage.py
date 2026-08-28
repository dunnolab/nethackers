from pathlib import Path

from nethackers.config import OFFLINE_OWNER, OFFLINE_TOKEN, Stage


def _read(name):
    return (Path(__file__).resolve().parents[1] / name).read_text()


def test_override_stub_map_matches_constants():
    text = _read("compose.override.yaml")
    assert f'"{OFFLINE_TOKEN}":"{OFFLINE_OWNER}"' in text.replace(" ", "")


def test_github_overlay_default_client_id_matches_stage():
    text = _read("compose.github.yaml")
    assert Stage().github_client_id in text


def test_base_compose_has_no_auth_env():
    text = _read("compose.yaml")
    assert "NETHACKERS_STUB_IDENTITIES" not in text
    assert "NETHACKERS_CLIENT_ID" not in text
