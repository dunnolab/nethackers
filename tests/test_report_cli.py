"""``nethackers report``: CLI wiring over ``crashfile.latest``/``load`` --
read-only and offline, unlike every hub-facing subcommand elsewhere in this
file. Hermetic throughout: ``cli.crashfile.latest``/``load`` are
monkeypatched, so no real crash directory is ever read by this file -- see
tests/test_crashfile.py for the module's own (non-CLI) tests, and
tests/test_cli.py for the `main()` top-level guard that actually WRITES a
crash file on an unexpected error (this file only covers displaying one that
already exists).
"""
from __future__ import annotations

import json
from pathlib import Path

import nethackers.cli as cli

_CRASH = {
    "ts": "2026-08-30T12:00:00+00:00",
    "nethackers_version": "0.17.0",
    "python": "3.11.9 (main, ...)",
    "platform": "macOS-15.0-arm64-arm-64bit",
    "argv": ["evolve", "--objective", "x", "--token", "<redacted>"],
    "exc_type": "RuntimeError",
    "traceback": "Traceback (most recent call last):\n  ...\nRuntimeError: boom\n",
    "doctor": {
        "checks": [],
        "capabilities": {"eval": True, "evolve": False, "publish": False, "browse": True},
        "env": {"nethackers": "0.17.0"},
    },
}


def _seed(monkeypatch, crash: dict | None = _CRASH) -> None:
    monkeypatch.setattr(cli.crashfile, "latest",
                        lambda: (None if crash is None else Path("/fake/crash.json")))
    monkeypatch.setattr(cli.crashfile, "load", lambda p: crash)


def test_report_json_emits_the_raw_crash_dict_on_stdout(monkeypatch, capsys):
    _seed(monkeypatch)

    rc = cli.main(["report", "-o", "json"])

    assert rc == 0
    out = capsys.readouterr().out
    assert json.loads(out) == _CRASH


def test_report_table_summary_contains_exc_type_and_version(monkeypatch, capsys):
    _seed(monkeypatch)

    rc = cli.main(["report", "-o", "table"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "RuntimeError" in out
    assert "0.17.0" in out


def test_report_table_summary_shows_doctor_capability_booleans(monkeypatch, capsys):
    _seed(monkeypatch)

    cli.main(["report", "-o", "table"])

    out = capsys.readouterr().out
    assert "eval=yes" in out
    assert "evolve=no" in out


def test_report_plain_output_has_no_rich_markup(monkeypatch, capsys):
    _seed(monkeypatch)

    cli.main(["report", "-o", "plain"])

    out = capsys.readouterr().out
    assert "RuntimeError" in out
    assert "[red]" not in out and "[/]" not in out


def test_report_bare_invocation_still_shows_the_essentials(monkeypatch, capsys):
    # No explicit -o: whatever `emit` resolves this to by default (json under
    # pytest's non-tty capsys), the exc_type + version must still be visible
    # somewhere in the output -- this is the exact case tests/test_cli.py's
    # crash-writing counterpart feeds back into (`nethackers report` with no
    # flags, right after main() prints the "wrote a crash report" hint).
    _seed(monkeypatch)

    rc = cli.main(["report"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "RuntimeError" in out
    assert "0.17.0" in out


def test_report_with_no_crash_files_says_so_and_leaves_stdout_clean(monkeypatch, capsys):
    _seed(monkeypatch, crash=None)

    rc = cli.main(["report", "-o", "json"])

    assert rc == 0
    captured = capsys.readouterr()
    assert captured.out == ""  # nothing to show -- never a bogus/empty JSON payload
    assert "no crash reports found" in captured.err


def test_report_never_calls_write_crash(monkeypatch, capsys):
    # Reviewer's-lens guard: `report` only ever DISPLAYS an existing file --
    # it must never itself trigger a fresh (network/docker-touching) doctor
    # probe or write a new crash file.
    _seed(monkeypatch)
    calls = []
    monkeypatch.setattr(cli.crashfile, "write_crash", lambda *a, **kw: calls.append(1))

    cli.main(["report"])

    assert calls == []
