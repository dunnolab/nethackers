from pathlib import Path

from nethackers.config import DEV_OWNER, DEV_TOKEN, Stage, load_stage


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
    assert s.arena_image == "nethackers/arena:dev"
    assert s.mutator_image == "nethackers/mutator:latest"
    assert s.github_client_id == "Iv23liWooDi2WlkrDAOw"


def test_dev_identity_constants():
    assert DEV_TOKEN == "dev-token"
    assert DEV_OWNER == "dev"


def test_load_stage_no_inputs_equals_prod_defaults():
    assert load_stage(environ={}) == Stage()


def test_env_overrides_defaults_with_casts():
    s = load_stage(environ={
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


def test_prod_flag_bypasses_discovery(tmp_path, monkeypatch):
    from nethackers.cli import _stage_from_argv

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env.stack").write_text("NETHACKERS_STAGE=wt\n")

    assert _stage_from_argv([]).name == "wt"          # discovery finds the worktree stage...
    assert _stage_from_argv(["--prod"]).name == "prod"  # ...but --prod forces prod regardless
