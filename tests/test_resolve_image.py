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
    from nethackers import _image_pins
    assert sp.resolve_image(None, "arena", repo_root=lambda: tmp_path) == (
        _image_pins.ARENA_IMAGE
    )
    assert (sp.resolve_image(None, "mutator", repo_root=lambda: tmp_path)
            == "nethackers/mutator:latest")


def test_non_repo_uses_the_pin():
    from nethackers import _image_pins
    assert sp.resolve_image(None, "arena", repo_root=lambda: None) == _image_pins.ARENA_IMAGE
    assert sp.resolve_image(None, "mutator", repo_root=lambda: None) == _image_pins.MUTATOR_IMAGE


def test_arena_resolves_to_the_pin_even_inside_a_repo_checkout(tmp_path):
    """The pin decides the reference architecture, so a repo checkout must not
    silently substitute a locally built (and unclassifiable) tag."""
    from nethackers import _image_pins
    assert (
        sp.resolve_image(None, "arena", repo_root=lambda: tmp_path)
        == _image_pins.ARENA_IMAGE
    )


def test_arena_explicit_override_still_wins_inside_a_repo(tmp_path):
    assert (
        sp.resolve_image("my/arena:wip", "arena", repo_root=lambda: tmp_path)
        == "my/arena:wip"
    )


def test_mutator_still_uses_the_dev_tag_inside_a_repo(tmp_path):
    """The mutator runs the coding agent, not scoring, so it stays native and
    keeps the local-build convenience (spec D10)."""
    assert (
        sp.resolve_image(None, "mutator", repo_root=lambda: tmp_path)
        == "nethackers/mutator:latest"
    )
