from pathlib import Path

from nethackers.config import OFFLINE_OWNER, OFFLINE_TOKEN, Stage
from nethackers.hub.fixtures import DEV_HIDDEN_SECRET, DEV_HIDDEN_SEEDS


def _read(name):
    return (Path(__file__).resolve().parents[1] / name).read_text()


def test_override_stub_map_matches_constants():
    text = _read("compose.override.yaml")
    assert f'"{OFFLINE_TOKEN}":"{OFFLINE_OWNER}"' in text.replace(" ", "")


def test_override_hidden_epoch_matches_fixtures():
    # fixtures.DEV_HIDDEN_SECRET/DEV_HIDDEN_SEEDS and this file's
    # NETHACKERS_HIDDEN_SECRET/NETHACKERS_HIDDEN_SEEDS must never drift apart --
    # a mismatch here isn't a loud failure, it's a SILENT one: the offline
    # fixture atoms would land under an epoch the hub never reads, so
    # ?tier=verified would just 503 or render empty with nothing pointing at
    # the cause.
    text = _read("compose.override.yaml")
    assert DEV_HIDDEN_SECRET in text
    for seed in DEV_HIDDEN_SEEDS:
        assert str(seed) in text


def test_github_overlay_default_client_id_matches_stage():
    text = _read("compose.github.yaml")
    assert Stage().github_client_id in text


def test_base_compose_has_no_auth_env():
    text = _read("compose.yaml")
    assert "NETHACKERS_STUB_IDENTITIES" not in text
    assert "NETHACKERS_CLIENT_ID" not in text
