from nethackers.config import load_stage
from nethackers.harness import sandbox_preflight as sp


def test_config_defaults_are_none(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # no .env.stack, no repo
    monkeypatch.delenv("NETHACKERS_ARENA_IMAGE", raising=False)
    monkeypatch.delenv("NETHACKERS_MUTATOR_IMAGE", raising=False)
    st = load_stage(cwd=tmp_path, environ={})
    assert st.arena_image is None and st.mutator_image is None


def test_explicit_wins(monkeypatch):
    assert sp.resolve_image("me/arena:x", "arena", repo_root=lambda: None) == "me/arena:x"


def test_repo_checkout_uses_local_dev_ref(tmp_path):
    assert sp.resolve_image(None, "arena", repo_root=lambda: tmp_path) == "nethackers/arena:dev"
    assert (sp.resolve_image(None, "mutator", repo_root=lambda: tmp_path)
            == "nethackers/mutator:latest")


def test_non_repo_uses_the_pin():
    from nethackers import _image_pins
    assert sp.resolve_image(None, "arena", repo_root=lambda: None) == _image_pins.ARENA_IMAGE
    assert sp.resolve_image(None, "mutator", repo_root=lambda: None) == _image_pins.MUTATOR_IMAGE
