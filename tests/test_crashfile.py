"""crashfile.py: local, consented crash files (zero telemetry -- nothing
here is ever transmitted; a user chooses to look at/share the file via
`nethackers report`, see test_report_cli.py). Hermetic: every test points
`NETHACKERS_DATA_ROOT` at a tmp_path (on top of `clean_stage`'s isolation),
so `load_stage()` resolves to a throwaway crash dir -- never a developer's
real ~/.nethackers."""
from __future__ import annotations

import os

import pytest

from nethackers import crashfile
from nethackers.diagnostics import version_info


@pytest.fixture
def crash_root(monkeypatch, clean_stage):
    root = clean_stage / "data"
    monkeypatch.setenv("NETHACKERS_DATA_ROOT", str(root))
    return root


def _raised(msg: str = "boom") -> RuntimeError:
    """A RuntimeError with a REAL ``__traceback__`` (unlike a bare
    ``RuntimeError(msg)`` construction), so ``traceback.format_exception``
    has actual frames to render -- closer to what `main()` ever hands
    `write_crash` in practice."""
    with pytest.raises(RuntimeError) as ei:
        raise RuntimeError(msg)
    return ei.value


# --- write_crash -------------------------------------------------------


def test_write_crash_writes_a_loadable_file_under_crashes(crash_root):
    exc = _raised()

    path = crashfile.write_crash(exc, argv=["evolve", "--objective", "x"])

    assert path is not None
    assert path.is_file()
    assert path.parent == crash_root / "crashes"
    assert path.suffix == ".json"


def test_loaded_crash_has_the_documented_fields(crash_root):
    exc = _raised("boom")

    path = crashfile.write_crash(exc, argv=["evolve", "--objective", "x"])

    loaded = crashfile.load(path)
    assert loaded["exc_type"] == "RuntimeError"
    assert "boom" in loaded["traceback"]
    assert loaded["nethackers_version"] == version_info()["nethackers"]
    assert loaded["argv"] == ["evolve", "--objective", "x"]
    assert loaded["doctor"] is None  # no enrich passed
    assert set(loaded) == {
        "ts", "nethackers_version", "python", "platform", "argv", "exc_type",
        "traceback", "doctor",
    }


def test_write_crash_never_raises_even_when_enrich_raises(crash_root):
    exc = _raised()

    def _bad_enrich():
        raise ValueError("enrich blew up")

    path = crashfile.write_crash(exc, argv=["evolve"], enrich=_bad_enrich)

    assert path is not None  # the write itself still succeeded
    assert crashfile.load(path)["doctor"] is None


def test_write_crash_stores_a_successful_enrich_result_as_doctor(crash_root):
    exc = _raised()

    path = crashfile.write_crash(
        exc, argv=["evolve"], enrich=lambda: {"capabilities": {"eval": True}},
    )

    assert crashfile.load(path)["doctor"] == {"capabilities": {"eval": True}}


def test_write_crash_returns_none_instead_of_raising_on_a_write_failure(
    crash_root, monkeypatch,
):
    # Simulate an unwritable data root (e.g. a permissions problem) --
    # write_crash must swallow it and return None, never propagate a SECOND
    # exception on top of the one main() is already handling.
    def _boom_mkdir(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(crashfile.Path, "mkdir", _boom_mkdir)

    assert crashfile.write_crash(_raised(), argv=["evolve"]) is None


# --- argv sanitization ---------------------------------------------------


def test_sanitize_argv_redacts_the_value_after_a_token_flag():
    out = crashfile._sanitize_argv(["evolve", "--token", "abcXYZ0123456789secret!"])
    assert out == ["evolve", "--token", "<redacted>"]


def test_sanitize_argv_redacts_a_flag_equals_value_form():
    out = crashfile._sanitize_argv(["--password=hunter2hunter2hunter2"])
    assert out == ["--password=<redacted>"]


def test_sanitize_argv_redacts_a_bare_opaque_token_with_no_flag_at_all():
    out = crashfile._sanitize_argv(["evolve", "ghp_16C7e42F292c6912E7710c838347Ae178B4a"])
    assert out == ["evolve", "<redacted>"]


def test_sanitize_argv_keeps_subcommands_flag_names_paths_and_short_ids():
    argv = [
        "eval", "roots/autoascend", "--objective", "val-dwa-law-fem",
        "--hub", "https://nethackers.dunnolab.ai", "--max-parallel-evals", "8",
    ]
    assert crashfile._sanitize_argv(argv) == argv


def test_write_crash_sanitizes_argv_end_to_end(crash_root):
    exc = _raised()
    argv = ["evolve", "--objective", "x", "--token", "sk-live-51H00000000000000000000"]

    path = crashfile.write_crash(exc, argv=argv)

    loaded_argv = crashfile.load(path)["argv"]
    assert loaded_argv == ["evolve", "--objective", "x", "--token", "<redacted>"]


# --- latest / load -------------------------------------------------------


def test_latest_is_none_when_no_crash_dir_exists_yet(crash_root):
    assert crashfile.latest() is None


def test_latest_is_none_when_crash_dir_exists_but_is_empty(crash_root):
    (crash_root / "crashes").mkdir(parents=True)
    assert crashfile.latest() is None


def test_latest_returns_the_newest_file_by_mtime(crash_root):
    first = crashfile.write_crash(_raised(), argv=["evolve"])
    second = crashfile.write_crash(_raised(), argv=["doctor"])
    assert first is not None and second is not None
    os.utime(first, (0, 0))  # deterministically push `first` into the past

    assert crashfile.latest() == second
