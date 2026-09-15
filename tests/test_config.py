from pathlib import Path

from nethackers.config import OFFLINE_OWNER, OFFLINE_TOKEN, Stage, load_stage


def test_stage_defaults_are_prod():
    s = Stage()
    assert s.name == "prod"
    assert s.hub_url == "https://nethackers.dunnolab.ai"
    assert s.hub_port == 8000
    assert s.compose_project == "nethackers"
    assert s.data_root == Path.home() / ".nethackers" / "evolve"
    assert s.runs_dir == Path.home() / ".nethackers" / "evolve" / "runs"
    assert s.store_dir == Path.home() / ".nethackers" / "evolve" / "store"
    assert s.repo_name == "nethacker"
    assert s.arena_image is None      # S14 sentinel; resolved via resolve_image()
    assert s.mutator_image is None    # S15 sentinel; resolved via resolve_image()
    assert s.github_client_id == "Iv23liWooDi2WlkrDAOw"


def test_offline_identity_constants():
    assert OFFLINE_TOKEN == "offline-token"
    assert OFFLINE_OWNER == "offline"


def test_load_stage_no_inputs_equals_prod_defaults(tmp_path, monkeypatch):
    # load_stage() with nothing meaningful supplied must resolve to Stage()'s
    # own prod defaults regardless of where/how this suite runs from: pin
    # `cwd` to an empty tmp_path (no ancestor there can ever hold a real
    # .env.stack, unlike this repo's own checkout after a `make up`/`make
    # stack`) and clear every NETHACKERS_* env key an ambient shell/worktree
    # might carry -- the same isolation test_prod_flag_bypasses_discovery
    # (below) and tests/test_cli_login.py's test_whoami_respects_o_json
    # already use.
    for key in _STAGE_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    assert load_stage(cwd=tmp_path) == Stage()


def test_env_overrides_defaults_with_casts():
    s = load_stage(environ={
        # Skip the .env.stack layer explicitly. Without this the test reads
        # whatever file `make stack` left in the worktree, so the final
        # assertion ("untouched -> default") passed or failed depending on
        # whether the developer had ever run `make stack` here.
        "NETHACKERS_STAGE_FILE": "",
        "NETHACKERS_STAGE": "tripletail",
        "NETHACKERS_HUB": "http://localhost:28417",
        "NETHACKERS_HUB_PORT": "28417",
        "COMPOSE_PROJECT_NAME": "nethackers-tripletail",
        "NETHACKERS_DATA_ROOT": "/tmp/wt/.nethackers",
        "NETHACKERS_REPO_NAME": "nh-dev-tripletail",
        "NETHACKERS_ARENA_IMAGE": "nethackers/arena:tripletail",
        "NETHACKERS_CLIENT_ID": "Iv_other",
    })
    assert s.name == "tripletail"
    assert s.hub_url == "http://localhost:28417"
    assert s.hub_port == 28417 and isinstance(s.hub_port, int)   # int cast
    assert s.compose_project == "nethackers-tripletail"
    assert s.data_root == Path("/tmp/wt/.nethackers")            # Path cast
    assert s.runs_dir == Path("/tmp/wt/.nethackers/runs")
    assert s.repo_name == "nh-dev-tripletail"
    assert s.arena_image == "nethackers/arena:tripletail"
    assert s.github_client_id == "Iv_other"
    assert s.mutator_image == Stage().mutator_image             # untouched -> default


def test_unknown_env_keys_ignored():
    s = load_stage(environ={"NETHACKERS_HUB": "http://x", "FOO_BAR": "baz"})
    assert s.hub_url == "http://x"


# --- cli's --prod pre-scan (Task 5): the argv-level bypass in front of the
# .env.stack discovery this module implements -- exercised here since it's
# the other half of "how a caller opts out of discovery" alongside
# NETHACKERS_STAGE_FILE="" above.

# Every NETHACKERS_* key load_stage() reads (the _ENV_TO_FIELD map, plus the
# NETHACKERS_STAGE_FILE discovery hatch) -- cleared below so an ambient
# shell/worktree env can never leak into a "bare load_stage()" assertion.
_STAGE_ENV_KEYS = (
    "NETHACKERS_STAGE", "NETHACKERS_STAGE_FILE", "NETHACKERS_HUB", "NETHACKERS_HUB_PORT",
    "COMPOSE_PROJECT_NAME", "NETHACKERS_DATA_ROOT", "NETHACKERS_REPO_NAME",
    "NETHACKERS_ARENA_IMAGE", "NETHACKERS_MUTATOR_IMAGE", "NETHACKERS_CLIENT_ID",
)


def test_prod_flag_bypasses_discovery(tmp_path, monkeypatch):
    from nethackers.cli import _stage_from_argv

    monkeypatch.chdir(tmp_path)
    for key in _STAGE_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    (tmp_path / ".env.stack").write_text("NETHACKERS_STAGE=wt\n")

    assert _stage_from_argv([]).name == "wt"          # discovery finds the worktree stage...
    assert _stage_from_argv(["--prod"]).name == "prod"  # ...but --prod forces prod regardless
