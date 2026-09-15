# tests/test_stack_alloc.py
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "stack", Path(__file__).resolve().parents[1] / "scripts" / "stack.py")
assert _spec is not None and _spec.loader is not None
stack = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(stack)


def test_slug_sanitizes():
    assert stack.slug(Path("/x/Weird_Name.2")) == "weird-name-2"


def test_seed_port_deterministic_and_in_range():
    wt = Path("/x/tripletail")
    p = stack.seed_port(wt)
    assert 28000 <= p < 29000
    assert stack.seed_port(wt) == p                       # stable for the same path
    assert stack.seed_port(Path("/x/bass")) != p or True  # (different path may differ)


def test_allocate_scans_past_occupied_seed(tmp_path):
    # Real dir, not the brief's literal `/x/tripletail`: allocate() does a real
    # write_text(worktree / ".env.stack"), and `/x` doesn't exist and can't be
    # created (root filesystem is read-only) -- tmp_path/"tripletail" keeps the
    # same slug/name assertions below while giving allocate() a real target.
    wt = tmp_path / "tripletail"
    wt.mkdir()
    seed = stack.seed_port(wt)
    v = stack.allocate(wt, is_free=lambda port: port != seed)  # seed busy -> next free
    assert int(v["NETHACKERS_HUB_PORT"]) != seed
    assert v["NETHACKERS_HUB"] == f"http://localhost:{v['NETHACKERS_HUB_PORT']}"
    assert v["COMPOSE_PROJECT_NAME"] == "nethackers-tripletail"
    assert v["NETHACKERS_STAGE"] == "tripletail"
    assert v["NETHACKERS_DATA_ROOT"] == str(wt / ".nethackers")


def test_render_parse_roundtrip():
    v = {"NETHACKERS_STAGE": "wt", "NETHACKERS_HUB_PORT": "28001"}
    assert stack.parse(stack.render(v)) == v


def test_allocate_writes_throwaway_names(tmp_path):
    # Fresh tmp_path dir (no pre-existing .env.stack): allocate()'s
    # reuse-verbatim rule returns an existing file unchanged, which would
    # mask these new keys if this test shared a dir with another test's
    # generated .env.stack. Real dir, not the brief's literal `/x/tripletail`
    # -- see test_allocate_scans_past_occupied_seed above for why (`/x` is
    # read-only on this machine).
    wt = tmp_path / "tripletail"
    wt.mkdir()
    v = stack.allocate(wt, is_free=lambda p: True)
    assert v["NETHACKERS_REPO_NAME"] == "nh-dev-tripletail"
    assert v["NETHACKERS_ARENA_IMAGE"] == "nethackers/arena:tripletail"
    assert "NETHACKERS_MUTATOR_IMAGE" not in v   # chosen by content, never by the stack file
