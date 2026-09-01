"""``nethackers doctor``: CLI wiring over ``diagnostics.run_checks``/
``exit_code``/``to_json``. Hermetic throughout -- ``cli.run_checks`` (and,
for the ``--pull`` test, ``cli.ensure_image``) is monkeypatched, so no real
docker/network call is ever made by this file. See ``tests/test_diagnostics.
py`` for the pure fold's own (non-CLI) tests, which is where the exhaustive
hard/soft x capability matrix lives -- this file only checks that the CLI
wires args -> ``run_checks`` -> ``exit_code``/``to_json`` correctly.
"""
from __future__ import annotations

import json

import nethackers.cli as cli
from nethackers.diagnostics import CheckResult


def _cr(id_, status, severity, caps, fix=None):
    return CheckResult(id=id_, status=status, severity=severity, detail=f"{id_} detail",
                       fix=fix, capabilities=caps)


_ALL_OK = [
    _cr("container_runtime", "ok", "hard", ("eval", "evolve")),
    _cr("arena_image", "ok", "hard", ("eval", "evolve")),
    _cr("mutator_image", "ok", "hard", ("evolve",)),
    _cr("hub", "ok", "soft", ("browse", "publish")),
    _cr("hub_login", "ok", "soft", ("publish",)),
    _cr("gh", "ok", "soft", ("publish",)),
    _cr("operator", "ok", "hard", ("evolve",)),
]


def _only_mutator_absent():
    return [
        r if r.id != "mutator_image" else
        _cr("mutator_image", "fail", "hard", ("evolve",), fix="pull the sandbox")
        for r in _ALL_OK
    ]


def test_doctor_json_all_ok(monkeypatch, capsys):
    monkeypatch.setattr(cli, "run_checks", lambda **kw: _ALL_OK)

    rc = cli.main(["doctor", "-o", "json"])

    data = json.loads(capsys.readouterr().out)
    assert set(data) == {"checks", "capabilities", "env"}
    assert len(data["checks"]) == 7
    assert data["capabilities"] == {"eval": True, "evolve": True, "publish": True, "browse": True}
    env_keys = {"nethackers", "run_schema_version", "images", "os", "arch", "python"}
    assert env_keys <= set(data["env"])
    assert rc == 0


def test_doctor_for_evolve_fails_when_mutator_absent(monkeypatch, capsys):
    monkeypatch.setattr(cli, "run_checks", lambda **kw: _only_mutator_absent())

    rc = cli.main(["doctor", "--for", "evolve", "-o", "json"])

    assert rc == 1
    data = json.loads(capsys.readouterr().out)
    assert data["capabilities"]["evolve"] is False


def test_doctor_bare_is_eval_ready_when_only_mutator_absent(monkeypatch, capsys):
    # Bare `doctor` gates on eval by default -- eval needs no mutator image at
    # all, so this must exit 0 even though evolve itself is reported not-ready.
    monkeypatch.setattr(cli, "run_checks", lambda **kw: _only_mutator_absent())

    rc = cli.main(["doctor", "-o", "json"])

    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["capabilities"]["eval"] is True
    assert data["capabilities"]["evolve"] is False


def test_doctor_human_output_shows_per_capability_verdicts(monkeypatch, capsys):
    monkeypatch.setattr(cli, "run_checks", lambda **kw: _only_mutator_absent())

    rc = cli.main(["doctor", "-o", "table"])

    out = capsys.readouterr().out
    assert rc == 0
    assert "ready to eval" in out
    assert "ready to evolve" in out
    assert "mutator_image" in out


def test_doctor_plain_output_has_no_rich_markup(monkeypatch, capsys):
    monkeypatch.setattr(cli, "run_checks", lambda **kw: _ALL_OK)

    cli.main(["doctor", "-o", "plain"])

    out = capsys.readouterr().out
    assert "ready to eval" in out
    assert "[green]" not in out and "[/]" not in out


def test_doctor_shortens_a_real_digest_in_human_output_but_not_json(monkeypatch, capsys):
    # End-to-end guard for the "147-char row breaks column alignment" defect
    # (task-2 fix round 1, finding #1): a realistic pinned-digest detail must
    # not reach a human-facing render un-shortened, through the FULL
    # cli.main -> emit -> render_human/render_plain pipeline -- not just the
    # renderer functions in isolation (content-only assertions elsewhere in
    # this file wouldn't have caught the original defect).
    digest = "a" * 64
    long_detail = f"present — ghcr.io/dunnolab/nethackers-arena@sha256:{digest}"
    checks = [CheckResult(id="arena_image", status="ok", severity="hard", detail=long_detail,
                          fix=None, capabilities=("eval", "evolve"))]
    monkeypatch.setattr(cli, "run_checks", lambda **kw: checks)

    cli.main(["doctor", "-o", "table"])
    table_out = capsys.readouterr().out
    assert digest not in table_out

    cli.main(["doctor", "-o", "json"])
    json_out = capsys.readouterr().out
    assert digest in json_out
    assert json.loads(json_out)["checks"][0]["detail"] == long_detail


def test_doctor_pull_ensures_both_images_then_rechecks(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(cli, "ensure_image", lambda ref, kind, **kw: calls.append(kind))
    monkeypatch.setattr(cli, "run_checks", lambda **kw: _ALL_OK)

    rc = cli.main(["doctor", "--pull", "-o", "json"])

    assert rc == 0
    assert set(calls) == {"arena", "mutator"}


def test_doctor_passes_operator_and_hub_flags_through(monkeypatch):
    seen = {}

    def fake_run_checks(**kw):
        seen.update(kw)
        return _ALL_OK

    monkeypatch.setattr(cli, "run_checks", fake_run_checks)

    cli.main(["doctor", "--operator", "codex", "--hub", "https://example.invalid"])

    assert seen["operator"] == "codex"
    assert seen["hub"] == "https://example.invalid"


def test_doctor_default_checks_all_registered_agents(monkeypatch):
    # bare `doctor` checks EVERY registered coding agent (operator=None), not
    # one -- the CLI passes operator=None straight through to run_checks.
    seen = {}

    def fake_run_checks(**kw):
        seen.update(kw)
        return _ALL_OK

    monkeypatch.setattr(cli, "run_checks", fake_run_checks)
    cli.main(["doctor"])
    assert seen["operator"] is None


def test_doctor_operator_flag_narrows_to_one(monkeypatch):
    seen = {}
    monkeypatch.setattr(cli, "run_checks", lambda **kw: seen.update(kw) or _ALL_OK)
    cli.main(["doctor", "--operator", "codex"])
    assert seen["operator"] == "codex"


def test_all_cli_operator_choices_derive_from_the_registry():
    # Principled-centralization guard: every `--operator` choices= list in the
    # CLI is exactly operators.OPERATORS, so the set can't silently re-scatter
    # (the bug this fixed -- a hardcoded pair duplicated across three argparse
    # blocks + the doctor check).
    import argparse

    from nethackers.config import load_stage
    from nethackers.operators import OPERATORS

    parser = cli._build_parser(load_stage())
    subs = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    checked = set()
    for name, sub in subs.choices.items():
        for a in sub._actions:
            if a.dest == "operator" and a.choices is not None:
                assert list(a.choices) == list(OPERATORS), f"{name} --operator choices drifted"
                checked.add(name)
    assert {"doctor", "models", "evolve"} <= checked


def test_doctor_never_raises_even_if_run_checks_returns_empty(monkeypatch, capsys):
    # Defensive: an empty result set (e.g. a future capability with zero
    # checks) must still produce clean output and a deterministic exit code,
    # never a traceback.
    monkeypatch.setattr(cli, "run_checks", lambda **kw: [])
    rc = cli.main(["doctor", "-o", "json"])
    assert rc == 0
    assert "Traceback" not in capsys.readouterr().err
