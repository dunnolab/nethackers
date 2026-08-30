"""``main()``'s generic-``Exception`` branch: the actual integration this
feature is about -- that an unexpected error really does call
``crashfile.write_crash`` and point the user at ``nethackers report``, not
just that ``crashfile.py``/``report`` are independently correct in
isolation (either could be right while the branch that wires them together
is silently missing -- the false-green ``crashfile``/``report``'s own unit
tests can't catch, since both units can pass even if `main()` never calls
`write_crash` at all).

Hermetic throughout: ``cli._run`` is monkeypatched to raise directly (never
reaching real argument parsing/dispatch), ``cli.run_checks`` is stubbed so
the crash path's best-effort ``enrich()`` never shells out to a real
docker/hub/gh probe, and every test points at a temp data root -- no real
docker/network call, and nothing is ever written under a developer's real
``~/.nethackers``."""
from __future__ import annotations

import pytest

import nethackers.cli as cli
from nethackers import crashfile


@pytest.fixture
def crash_root(monkeypatch, clean_stage):
    root = clean_stage / "data"
    monkeypatch.setenv("NETHACKERS_DATA_ROOT", str(root))
    return root


def _raise_boom(*_a, **_kw):
    raise RuntimeError("boom")


def _raise_write_crash_bug(*_a, **_kw):
    raise ValueError("write_crash itself is broken")


def test_main_calls_write_crash_and_points_to_report(monkeypatch, capsys, crash_root):
    # The feature's actual acceptance test: an unexpected error reaching
    # main()'s top-level guard must really produce a crash file (not just
    # that write_crash/report work when called directly).
    monkeypatch.setattr(cli, "_run", _raise_boom)
    monkeypatch.setattr(cli, "run_checks", lambda **kw: [])  # no real docker/hub/gh

    rc = cli.main(["doctor"])

    assert rc == 1
    path = crashfile.latest()
    assert path is not None
    loaded = crashfile.load(path)
    assert loaded["exc_type"] == "RuntimeError"
    assert "boom" in loaded["traceback"]
    assert "nethackers report" in capsys.readouterr().err


def test_main_survives_write_crash_itself_raising(monkeypatch, capsys, crash_root):
    # Belt-and-suspenders guard (cli.py's own try/except around the
    # write_crash call, on top of write_crash's own internal never-raise
    # promise): a bug IN the crash writer must never replace the ORIGINAL
    # exception main() was already handling with a second, unrelated one.
    monkeypatch.setattr(cli, "_run", _raise_boom)
    monkeypatch.setattr(cli.crashfile, "write_crash", _raise_write_crash_bug)

    rc = cli.main(["doctor"])  # must not raise ValueError (or anything else)

    assert rc == 1
    err = capsys.readouterr().err
    assert "RuntimeError" in err and "boom" in err  # the ORIGINAL exception's one-liner
    assert "nethackers report" not in err  # path stayed None -- no pointer to a file that
                                            # was never written
    assert crashfile.latest() is None  # nothing was actually written either


def test_main_still_reraises_under_nethackers_debug(monkeypatch, crash_root):
    # Unchanged pre-existing behavior: NETHACKERS_DEBUG=1 bypasses the whole
    # crash-file mechanism and re-raises immediately, for a full local
    # traceback -- this must stay true after wiring in the crash writer.
    monkeypatch.setenv("NETHACKERS_DEBUG", "1")
    monkeypatch.setattr(cli, "_run", _raise_boom)

    with pytest.raises(RuntimeError, match="boom"):
        cli.main(["doctor"])

    assert crashfile.latest() is None  # the debug path never writes a crash file
