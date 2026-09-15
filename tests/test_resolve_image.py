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


def test_repo_checkout_arena_uses_local_dev_ref(tmp_path):
    assert sp.resolve_image(None, "arena", repo_root=lambda: tmp_path) == "nethackers/arena:dev"


def test_checkout_mutator_matching_the_pin_uses_the_pinned_digest(tmp_path, monkeypatch):
    from nethackers import _image_pins
    monkeypatch.setattr(sp.image_inputs, "mutator_inputs_hash",
                        lambda root, base: _image_pins.MUTATOR_INPUTS)
    assert (sp.resolve_image(None, "mutator", repo_root=lambda: tmp_path)
            == _image_pins.MUTATOR_IMAGE)


def test_checkout_mutator_with_changed_files_uses_its_fingerprint_tag(tmp_path, monkeypatch):
    monkeypatch.setattr(sp.image_inputs, "mutator_inputs_hash",
                        lambda root, base: "sha256:" + "d" * 64)
    assert (sp.resolve_image(None, "mutator", repo_root=lambda: tmp_path)
            == "nethackers/mutator:h-" + "d" * 64)


def test_checkout_mutator_is_hashed_on_the_pinned_base(tmp_path, monkeypatch):
    from nethackers import _image_pins
    seen = {}

    def fake_hash(root, base):
        seen.update(root=root, base=base)
        return "sha256:" + "d" * 64

    monkeypatch.setattr(sp.image_inputs, "mutator_inputs_hash", fake_hash)
    sp.resolve_image(None, "mutator", repo_root=lambda: tmp_path)
    assert seen == {"root": tmp_path, "base": _image_pins.NLE_BASE_IMAGE}


def test_incomplete_checkout_falls_back_to_the_pinned_mutator(tmp_path):
    from nethackers import _image_pins
    # tmp_path has none of the mutator's input files
    assert (sp.resolve_image(None, "mutator", repo_root=lambda: tmp_path)
            == _image_pins.MUTATOR_IMAGE)


def test_non_repo_uses_the_pin():
    from nethackers import _image_pins
    assert sp.resolve_image(None, "arena", repo_root=lambda: None) == _image_pins.ARENA_IMAGE
    assert sp.resolve_image(None, "mutator", repo_root=lambda: None) == _image_pins.MUTATOR_IMAGE
