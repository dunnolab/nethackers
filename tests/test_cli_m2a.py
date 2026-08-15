"""Tests for the M2a hub-facing CLI subcommands added to ``nethackers.cli``
in Task 13: ``map``/``attainment``, ``elites``, ``board``, ``search``,
``show``, ``register``, plus the global ``--hub`` option. ``HubClient`` and
``register_solution`` are monkeypatched on the ``cli`` module throughout --
no real HTTP/network call is ever made, mirroring how ``tests/test_cli.py``
monkeypatches ``eval_batch``/``pull`` for the pre-existing subcommands. That
file's ``eval``/``pull`` coverage is untouched and must stay green alongside
this one.

CLI-UX pass (folding in ``rich``; see fix-b-rich-output-context.md): the
boolean ``--json`` is gone, replaced by ``-o``/``--output``
``{auto,table,json,plain}`` dispatched through
``nethackers.hubclient.output.emit``. Tests below are grouped to match that
fix's six required groups:

1. **emit dispatch** -- ``nethackers.hubclient.output.resolve``'s 3-tier
   precedence (explicit ``-o`` > ``$NETHACKERS_OUTPUT`` > TTY-detected
   ``"auto"``), unit-tested directly and through the CLI.
2. **json is raw + jq-able** -- ``-o json`` is exactly
   ``json.loads``-equal to the stubbed response, ANSI-free, full digests.
3. **table renders** -- the ``rich`` renderers (``hubclient.render``),
   tested directly (precise content assertions, captured ANSI-free via a
   throwaway non-terminal ``Console`` -- ``_render_text`` below) and
   through the CLI (``-o table`` / forced-terminal ``auto``).
4. **plain == baseline** -- ``-o plain`` reproduces the baseline
   pure-Python ``_table`` renders (``hubclient.client``, imported here as
   ``plain_*``) byte-for-byte; these bodies are the pre-CLI-UX-pass
   "renders ascii table by default" tests, adapted to the new explicit
   flag (the *default* is no longer the plain table -- see group 1).
5. **chrome on stderr** -- ``register``'s device-flow prompt, plus proof
   that ``-o json`` mode's stdout carries the JSON payload and nothing
   else.
6. The loop-closing regression (CLI ``eval --objective`` evidence is
   actually registerable) and the ``--hub``-after-subcommand test, both
   **unchanged** per the fix's explicit instruction.
"""

from __future__ import annotations

import io
import json

import httpx
import pytest
from rich.console import Console

import nethackers.cli as C
from nethackers.contracts.models import Evidence, Objective, TrajectoryResult
from nethackers.hub.auth import LocalStubAuth
from nethackers.hub.objectives import CATALOG
from nethackers.hub.store import Store
from nethackers.hub.validate import LocalStubGit, SolutionReference, register
from nethackers.hubclient import output as O
from nethackers.hubclient.client import (
    _num,
    _short_digest,
    _table,
    plain_frontier,
    render_board as plain_board,
    render_elites as plain_elites,
    render_search as plain_search,
    render_show as plain_show,
)
from nethackers.hubclient.render import (
    ramp,
    render_board as rich_board,
    render_elites as rich_elites,
    render_search as rich_search,
    render_show as rich_show,
)


def _make_fake_hub_client(response_map):
    """Return ``(FakeHubClient, calls)``: a stand-in for ``HubClient`` whose
    read methods each append ``(name, *args)`` to the shared ``calls`` list
    and return ``response_map[name]`` (default ``[]``/``{}``), plus its own
    construction as ``("__init__", base_url)``.
    """
    calls: list[tuple[object, ...]] = []

    class FakeHubClient:
        def __init__(self, base_url):
            calls.append(("__init__", base_url))
            self.base_url = base_url

        def elites(self, objective):
            calls.append(("elites", objective))
            return response_map.get("elites", [])

        def board(self, objective=None, metric=None):
            calls.append(("board", objective, metric))
            return response_map.get("board", [])

        def solution_frontier(self, digest):
            calls.append(("solution_frontier", digest))
            return response_map.get("solution_frontier", [])

        def search(self, owner=None, limit=50, offset=0):
            calls.append(("search", owner, limit, offset))
            return response_map.get("search", [])

        def show(self, digest):
            calls.append(("show", digest))
            return response_map.get("show", {})

    return FakeHubClient, calls


def _render_text(renderable, width=100):
    """Render a ``rich`` renderable to a plain string through a throwaway,
    deterministically non-color ``Console`` (``force_terminal=False`` makes
    ``is_terminal`` -- and so color-system detection -- always ``False``
    regardless of the real environment, unlike the shared module-level
    ``console``, whose color system is fixed once at import time from
    whatever *that* happened to be) -- so assertions never have to account
    for ANSI escape codes."""
    buf = io.StringIO()
    Console(file=buf, force_terminal=False, no_color=True, width=width).print(renderable)
    return buf.getvalue()


# --- Property 3: each subcommand dispatches to the right client method ----


def test_cli_frontier_no_flag_dispatches_to_universe_scores(monkeypatch, capsys):
    FakeHubClient, calls = _make_fake_hub_client({"elites": []})
    monkeypatch.setattr(C, "HubClient", FakeHubClient)

    rc = C.main(["frontier"])

    assert rc == 0
    assert ("elites", "all") in calls


def test_cli_attainment_alias_dispatches_to_universe_scores(monkeypatch, capsys):
    # The one dedicated alias test -- 'map'/'attainment' must keep dispatching
    # exactly like 'frontier' itself (no --program -> the universe regime).
    FakeHubClient, calls = _make_fake_hub_client({"elites": []})
    monkeypatch.setattr(C, "HubClient", FakeHubClient)

    rc = C.main(["attainment"])

    assert rc == 0
    assert ("elites", "all") in calls


def test_cli_frontier_program_digest_dispatches_to_solution_frontier(monkeypatch, capsys):
    # `--program <digest>` (a non-empty value) skips champion resolution
    # entirely and goes straight to that solution's own frontier.
    digest = "sha256:abcdef0123456789"
    response = [{"identity": "val-hum-neu-fem", "progression": 0.42, "episodes": 3}]
    FakeHubClient, calls = _make_fake_hub_client({"solution_frontier": response})
    monkeypatch.setattr(C, "HubClient", FakeHubClient)

    rc = C.main(["frontier", "--program", digest, "-o", "json"])

    assert rc == 0
    assert ("solution_frontier", digest) in calls
    assert ("board", "random", None) not in calls  # no champion lookup needed
    assert json.loads(capsys.readouterr().out) == {"val-hum-neu-fem": 0.42}


def test_cli_frontier_bare_program_flag_resolves_champion_with_owner_note(monkeypatch, capsys):
    # `--program` with NO value (argparse's `const=""`) means "the champion":
    # look it up via `champion()`, then render its grid with an "@owner" note.
    board_rows = [{"rank": 1, "solution_digest": "sha256:abcdef0123456789", "owner": "sam"}]
    frontier_rows = [{"identity": "val-hum-neu-fem", "progression": 0.42, "episodes": 3}]
    FakeHubClient, calls = _make_fake_hub_client(
        {"board": board_rows, "solution_frontier": frontier_rows}
    )
    monkeypatch.setattr(C, "HubClient", FakeHubClient)
    monkeypatch.setattr(O.console, "_width", 200)  # wide: the note fits on one line

    rc = C.main(["frontier", "--program", "-o", "table"])

    assert rc == 0
    assert ("board", "random", None) in calls
    assert ("solution_frontier", "sha256:abcdef0123456789") in calls
    out = capsys.readouterr().out
    assert "@sam" in out  # the champion's owner, called out by name
    assert "Valkyrie" in out and "hum-neu-fem" in out and "0.42" in out


def test_cli_frontier_bare_program_flag_no_ranked_programs_is_friendly(monkeypatch, capsys):
    FakeHubClient, calls = _make_fake_hub_client({"board": []})
    monkeypatch.setattr(C, "HubClient", FakeHubClient)

    rc = C.main(["frontier", "--program", "-o", "table"])

    assert rc == 0
    assert ("board", "random", None) in calls
    assert "no ranked programs yet" in capsys.readouterr().out


def test_cli_frontier_bare_program_flag_no_ranked_programs_json_is_empty_object(
    monkeypatch, capsys
):
    # The friendly message is table/plain-only chrome -- -o json always stays
    # the raw (empty) data, same convention as every other renderer.
    FakeHubClient, _calls = _make_fake_hub_client({"board": []})
    monkeypatch.setattr(C, "HubClient", FakeHubClient)

    rc = C.main(["frontier", "--program", "-o", "json"])

    assert rc == 0
    assert json.loads(capsys.readouterr().out) == {}


def test_cli_frontier_hub_down_is_friendly_not_traceback(monkeypatch, capsys):
    # The frontier dispatch goes through hubclient.frontier's adapters now,
    # not a single direct client call -- prove main()'s top-level guard still
    # catches a failure raised from inside that adapter chain.
    request = httpx.Request("GET", "http://localhost:8000/elites")

    class FakeHub:
        def __init__(self, base_url):
            pass

        def elites(self, objective):
            raise httpx.ConnectError("Connection refused", request=request)

    monkeypatch.setattr(C, "HubClient", FakeHub)
    rc = C.main(["frontier"])
    assert rc == 1
    captured = capsys.readouterr()
    assert "cannot reach the hub" in captured.err
    assert "Traceback" not in captured.err
    assert captured.out == ""


def test_cli_no_args_prints_help_with_project_description(capsys):
    rc = C.main([])
    assert rc == 0
    out = capsys.readouterr().out
    assert "usage" in out.lower()
    assert "NetHackers" in out  # the project description, not just the bare command list
    assert "solve" in out.lower() and "hub" in out.lower()
    assert "board" in out and "register" in out  # commands still listed


def test_cli_unknown_objective_is_friendly_not_a_hub_roundtrip(capsys):
    # A typo like 'wiz' (the identity is 'wiz-elf-cha-mal') is caught
    # client-side against the catalog -- a clean message + rc 2, no HTTP call.
    rc = C.main(["board", "--objective", "wiz"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "unknown objective" in err and "wiz" in err
    assert "Traceback" not in err


def test_cli_hub_http_error_is_friendly_not_traceback(monkeypatch, capsys):
    request = httpx.Request("GET", "http://localhost:8000/board?objective=random")
    response = httpx.Response(404, request=request)

    class FakeHub:
        def __init__(self, base_url):
            pass

        def board(self, objective=None, metric=None):
            raise httpx.HTTPStatusError("404", request=request, response=response)

    monkeypatch.setattr(C, "HubClient", FakeHub)
    rc = C.main(["board", "--objective", "random"])  # valid name -> reaches the hub call
    assert rc == 1  # caught: main returns cleanly (an uncaught error would raise here)
    captured = capsys.readouterr()
    assert "hub error" in captured.err and "404" in captured.err
    assert "Traceback" not in captured.err
    assert captured.out == ""  # nothing half-rendered on stdout


def test_cli_hub_connection_error_is_friendly(monkeypatch, capsys):
    request = httpx.Request("GET", "http://localhost:8000/board")

    class FakeHub:
        def __init__(self, base_url):
            pass

        def board(self, objective=None, metric=None):
            raise httpx.ConnectError("Connection refused", request=request)

    monkeypatch.setattr(C, "HubClient", FakeHub)
    rc = C.main(["board", "--metric", "coverage"])
    assert rc == 1
    assert "cannot reach the hub" in capsys.readouterr().err


def test_cli_bad_hub_url_is_friendly(monkeypatch, capsys):
    # e.g. `--hub localhost` (no scheme) -> httpx.UnsupportedProtocol, which the
    # top-level guard turns into a fix-it hint instead of a transport traceback.
    class FakeHub:
        def __init__(self, base_url):
            pass

        def board(self, objective=None, metric=None):
            raise httpx.UnsupportedProtocol(
                "Request URL is missing an 'http://' or 'https://' protocol."
            )

    monkeypatch.setattr(C, "HubClient", FakeHub)
    rc = C.main(["board", "--metric", "coverage"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "invalid hub URL" in err and "http://" in err  # tells the user how to fix it
    assert "Traceback" not in err


def test_cli_unexpected_error_is_caught_unless_debug(monkeypatch, capsys):
    # Any non-hub error (a bug, a bad response shape, …) must still exit
    # cleanly, never a raw traceback -- that's the whole point of the guard.
    class FakeHub:
        def __init__(self, base_url):
            pass

        def board(self, objective=None, metric=None):
            raise ValueError("boom")

    monkeypatch.setattr(C, "HubClient", FakeHub)

    monkeypatch.delenv("NETHACKERS_DEBUG", raising=False)
    rc = C.main(["board", "--metric", "coverage"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "unexpected error" in err and "ValueError" in err and "boom" in err
    assert "Traceback" not in err

    # opt-in: NETHACKERS_DEBUG=1 re-raises so a developer gets the full traceback
    monkeypatch.setenv("NETHACKERS_DEBUG", "1")
    with pytest.raises(ValueError, match="boom"):
        C.main(["board", "--metric", "coverage"])


def test_cli_elites_dispatches_with_objective(monkeypatch, capsys):
    FakeHubClient, calls = _make_fake_hub_client({"elites": [{"identity": "x"}]})
    monkeypatch.setattr(C, "HubClient", FakeHubClient)

    rc = C.main(["elites", "--objective", "random", "-o", "json"])

    assert rc == 0
    assert ("elites", "random") in calls
    payload = json.loads(capsys.readouterr().out)
    assert payload == [{"identity": "x"}]


def test_cli_board_dispatches_with_objective(monkeypatch, capsys):
    FakeHubClient, calls = _make_fake_hub_client({"board": []})
    monkeypatch.setattr(C, "HubClient", FakeHubClient)

    rc = C.main(["board", "--objective", "random"])

    assert rc == 0
    assert ("board", "random", None) in calls


def test_cli_board_dispatches_with_metric(monkeypatch, capsys):
    FakeHubClient, calls = _make_fake_hub_client({"board": []})
    monkeypatch.setattr(C, "HubClient", FakeHubClient)

    rc = C.main(["board", "--metric", "coverage"])

    assert rc == 0
    assert ("board", None, "coverage") in calls


def test_cli_search_dispatches_with_owner_and_paging(monkeypatch, capsys):
    FakeHubClient, calls = _make_fake_hub_client({"search": [{"digest": "sha256:x"}]})
    monkeypatch.setattr(C, "HubClient", FakeHubClient)

    rc = C.main(["search", "--owner", "sam", "--limit", "10", "--offset", "5", "-o", "json"])

    assert rc == 0
    assert ("search", "sam", 10, 5) in calls
    payload = json.loads(capsys.readouterr().out)
    assert payload == [{"digest": "sha256:x"}]


def test_cli_search_defaults(monkeypatch, capsys):
    FakeHubClient, calls = _make_fake_hub_client({"search": []})
    monkeypatch.setattr(C, "HubClient", FakeHubClient)

    rc = C.main(["search"])

    assert rc == 0
    assert ("search", None, 50, 0) in calls


def test_cli_show_dispatches_with_digest(monkeypatch, capsys):
    FakeHubClient, calls = _make_fake_hub_client({"show": {"digest": "sha256:abc"}})
    monkeypatch.setattr(C, "HubClient", FakeHubClient)

    rc = C.main(["show", "sha256:abc", "-o", "json"])

    assert rc == 0
    assert ("show", "sha256:abc") in calls
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"digest": "sha256:abc"}


def test_cli_register_dispatches_with_gathered_reference_manifest_evidence(
    monkeypatch, capsys, tmp_path
):
    solution_dir = tmp_path / "solution"
    solution_dir.mkdir()
    manifest = {"root": ".", "entrypoint": "bot.py"}
    (solution_dir / "nethackers.solution.json").write_text(json.dumps(manifest))

    evidence = {"solution_digest": "sha256:abc"}
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(json.dumps(evidence))

    FakeHubClient, _calls = _make_fake_hub_client({})
    monkeypatch.setattr(C, "HubClient", FakeHubClient)

    seen = {}

    def fake_register_solution(*, hub, reference, manifest, evidence, prompt=print):
        seen["hub"] = hub
        seen["reference"] = reference
        seen["manifest"] = manifest
        seen["evidence"] = evidence
        seen["prompt"] = prompt
        return {"solution_digest": "sha256:abc", "owner": "sam"}

    monkeypatch.setattr(C, "register_solution", fake_register_solution)

    rc = C.main(
        [
            "register",
            "--repo",
            "github.com/sam/nethacker",
            "--commit",
            "a" * 40,
            "--solution",
            str(solution_dir),
            "--evidence",
            str(evidence_path),
        ]
    )

    assert rc == 0
    assert seen["reference"] == {"repo": "github.com/sam/nethacker", "commit": "a" * 40}
    assert seen["manifest"] == manifest
    assert seen["evidence"] == evidence
    assert isinstance(seen["hub"], FakeHubClient)
    assert callable(seen["prompt"])  # cli.py must wire a prompt sink -- see Property 5
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"solution_digest": "sha256:abc", "owner": "sam"}


def test_cli_register_accepts_manifest_file_directly(monkeypatch, capsys, tmp_path):
    manifest = {"root": ".", "entrypoint": "bot.py"}
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(json.dumps({"solution_digest": "sha256:abc"}))

    FakeHubClient, _calls = _make_fake_hub_client({})
    monkeypatch.setattr(C, "HubClient", FakeHubClient)

    seen = {}

    def fake_register_solution(*, hub, reference, manifest, evidence, prompt=print):
        seen["manifest"] = manifest
        return {"ok": True}

    monkeypatch.setattr(C, "register_solution", fake_register_solution)

    rc = C.main(
        [
            "register",
            "--repo",
            "x/y",
            "--commit",
            "a" * 40,
            "--manifest",
            str(manifest_path),
            "--evidence",
            str(evidence_path),
        ]
    )

    assert rc == 0
    assert seen["manifest"] == manifest


# --- Group 1: emit dispatch (resolve()'s 3-tier precedence) ----------------


def test_resolve_auto_is_table_when_stdout_is_a_terminal(monkeypatch):
    monkeypatch.setattr(O.console, "_force_terminal", True)
    assert O.resolve("auto") == "table"


def test_resolve_auto_is_json_when_stdout_is_not_a_terminal(monkeypatch):
    monkeypatch.setattr(O.console, "_force_terminal", False)
    assert O.resolve("auto") == "json"
    assert O.resolve(None) == "json"  # missing/unset behaves exactly like "auto"


def test_resolve_explicit_non_auto_choices_pass_through(monkeypatch):
    monkeypatch.setattr(O.console, "_force_terminal", False)  # would be "json" under plain auto
    assert O.resolve("table") == "table"
    assert O.resolve("json") == "json"
    assert O.resolve("plain") == "plain"


def test_resolve_env_var_forces_json_even_under_a_tty(monkeypatch):
    monkeypatch.setattr(O.console, "_force_terminal", True)  # auto would pick "table"
    monkeypatch.setenv("NETHACKERS_OUTPUT", "json")

    assert O.resolve("auto") == "json"
    assert O.resolve(None) == "json"


def test_resolve_explicit_output_flag_beats_the_env_var(monkeypatch):
    monkeypatch.setattr(O.console, "_force_terminal", False)
    monkeypatch.setenv("NETHACKERS_OUTPUT", "json")

    assert O.resolve("table") == "table"
    assert O.resolve("plain") == "plain"


def test_cli_env_var_sets_the_default_output_format(monkeypatch, capsys):
    FakeHubClient, _calls = _make_fake_hub_client({"board": []})
    monkeypatch.setattr(C, "HubClient", FakeHubClient)
    monkeypatch.setenv("NETHACKERS_OUTPUT", "plain")

    rc = C.main(["board", "--objective", "random"])  # no -o given anywhere

    assert rc == 0
    # plain's empty-board message, not json's "[]" -- proves the env var
    # actually reached the CLI's own "auto" default, not just resolve()
    # in isolation.
    assert capsys.readouterr().out.strip() == "no board entries yet."


def test_cli_explicit_output_flag_beats_env_var(monkeypatch, capsys):
    FakeHubClient, _calls = _make_fake_hub_client({"board": []})
    monkeypatch.setattr(C, "HubClient", FakeHubClient)
    monkeypatch.setenv("NETHACKERS_OUTPUT", "plain")

    rc = C.main(["board", "--objective", "random", "-o", "json"])

    assert rc == 0
    assert json.loads(capsys.readouterr().out) == []


def test_cli_output_flag_before_subcommand(monkeypatch, capsys):
    FakeHubClient, _calls = _make_fake_hub_client({"board": []})
    monkeypatch.setattr(C, "HubClient", FakeHubClient)

    rc = C.main(["-o", "json", "board", "--objective", "random"])

    assert rc == 0
    assert json.loads(capsys.readouterr().out) == []


def test_cli_output_flag_after_subcommand(monkeypatch, capsys):
    # Mirrors the --hub-after-subcommand coverage (Property 5 below) for -o.
    FakeHubClient, _calls = _make_fake_hub_client({"board": []})
    monkeypatch.setattr(C, "HubClient", FakeHubClient)

    rc = C.main(["board", "--objective", "random", "--output", "json"])

    assert rc == 0
    assert json.loads(capsys.readouterr().out) == []


def test_cli_output_flag_rejects_invalid_choice():
    with pytest.raises(SystemExit):
        C.main(["board", "-o", "yaml"])


def test_cli_help_uses_rich_formatter_without_crashing(capsys):
    with pytest.raises(SystemExit) as exc_info:
        C.main(["--help"])
    assert exc_info.value.code == 0
    assert "nethackers" in capsys.readouterr().out


def test_cli_subcommand_help_without_crashing(capsys):
    with pytest.raises(SystemExit) as exc_info:
        C.main(["board", "--help"])
    assert exc_info.value.code == 0
    assert "--objective" in capsys.readouterr().out


# --- Group 2: json is raw + jq-able (full digests, never ANSI) -------------


def test_cli_map_json_on_empty_response_emits_empty_json_object(monkeypatch, capsys):
    # -o json bypasses any friendly-message/table default entirely -- it
    # always prints exactly the raw {identity: value} map, empty or not
    # (an empty universe is {}, not [] -- frontier's payload is a map).
    FakeHubClient, _calls = _make_fake_hub_client({"elites": []})
    monkeypatch.setattr(C, "HubClient", FakeHubClient)

    rc = C.main(["map", "-o", "json"])

    assert rc == 0
    assert json.loads(capsys.readouterr().out) == {}


def test_cli_frontier_json_emits_identity_value_map(monkeypatch, capsys):
    # The populated case: -o json prints exactly the {identity: value} map
    # universe_scores assembled (rank-1 elites only), parseable by jq.
    rows = [
        {"identity": "val-hum-neu-fem", "solution_digest": "sha256:a", "score": 0.8, "rank": 1},
        {"identity": "val-hum-neu-fem", "solution_digest": "sha256:b", "score": 0.5, "rank": 2},
        {"identity": "wiz-elf-cha-mal", "solution_digest": "sha256:c", "score": 0.65, "rank": 1},
    ]
    FakeHubClient, _calls = _make_fake_hub_client({"elites": rows})
    monkeypatch.setattr(C, "HubClient", FakeHubClient)

    rc = C.main(["frontier", "-o", "json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"val-hum-neu-fem": 0.8, "wiz-elf-cha-mal": 0.65}


def test_cli_board_json_emits_raw_response_equal_to_stub(monkeypatch, capsys):
    response = [
        {"rank": 1, "solution_digest": "sha256:0123456789abcdef", "owner": "sam",
         "episodes": 8, "ascensions": 1, "median_progression": 0.5, "mean_progression": 0.4}
    ]
    FakeHubClient, _calls = _make_fake_hub_client({"board": response})
    monkeypatch.setattr(C, "HubClient", FakeHubClient)

    rc = C.main(["board", "--objective", "random", "-o", "json"])

    assert rc == 0
    assert json.loads(capsys.readouterr().out) == response


def test_cli_json_output_has_no_ansi_and_full_digest_even_under_forced_terminal(
    monkeypatch, capsys
):
    # The regression this guards: JSON must never go through rich.print_json
    # (which would color it) even when "auto" would otherwise pick "table".
    monkeypatch.setattr(O.console, "_force_terminal", True)
    full_digest = "sha256:" + "a" * 64
    response = [
        {"rank": 1, "solution_digest": full_digest, "owner": "sam", "episodes": 8,
         "ascensions": 1, "median_progression": 0.5087697678994835, "mean_progression": 0.4}
    ]
    FakeHubClient, _calls = _make_fake_hub_client({"board": response})
    monkeypatch.setattr(C, "HubClient", FakeHubClient)

    rc = C.main(["board", "--objective", "random", "-o", "json"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "\x1b" not in out  # no ANSI/color escapes -- jq-safe
    assert json.loads(out) == response
    assert "a" * 64 in out  # the full 64-char digest, not a 12-char short form


# --- Group 3: table renders (rich renderers) --------------------------------
# Direct renderer-function tests: precise content assertions, captured
# ANSI-free via `_render_text` (a throwaway non-terminal Console).


def test_rich_render_board_grading_shape():
    entries = [
        {"rank": 1, "solution_digest": "sha256:0123456789abcdef", "owner": "sam",
         "episodes": 8, "ascensions": 1, "median_progression": 0.5087697678994835,
         "mean_progression": 0.4}
    ]
    out = _render_text(rich_board(entries))
    assert "solution" in out and "median" in out and "mean" in out  # shape-aware header
    assert "sam" in out
    assert _short_digest(entries[0]["solution_digest"]) in out
    assert "0.509" in out  # rounded
    assert "0.5087697678994835" not in out  # not full precision
    # Old papercut: naive `str(digest)[:12]` collapses onto the shared
    # "sha256:" prefix -- must not appear.
    assert entries[0]["solution_digest"][:12] not in out


def test_rich_render_board_coverage_shape_shows_count_column():
    entries = [
        {"rank": 1, "solution_digest": "sha256:0123456789abcdef", "owner": "sam",
         "cells_held": 3}
    ]
    out = _render_text(rich_board(entries))
    assert "cells" in out  # shape-aware count column header
    assert "3" in out
    assert "sam" in out


def test_rich_render_board_firsts_shape_shows_count_column():
    entries = [
        {"rank": 1, "solution_digest": "sha256:0123456789abcdef", "owner": "sam", "firsts": 7}
    ]
    out = _render_text(rich_board(entries))
    assert "firsts" in out
    assert "7" in out


def test_rich_render_board_empty_is_friendly_not_bare_header():
    assert _render_text(rich_board([])).strip() == "no board entries yet."


def test_rich_render_elites_contains_expected_cells():
    entries = [
        {"identity": "val-dwa-law-fem", "solution_digest": "sha256:0123456789abcdef",
         "score": 0.5087697678994835, "rank": 1}
    ]
    out = _render_text(rich_elites(entries))
    assert "identity" in out and "score" in out  # header
    assert "val-dwa-law-fem" in out
    assert _short_digest(entries[0]["solution_digest"]) in out
    assert "0.509" in out
    assert "0.5087697678994835" not in out


def test_rich_render_elites_empty_is_friendly_not_bare_header():
    assert _render_text(rich_elites([])).strip() == "no elites recorded yet."


def test_rich_render_search_contains_expected_cells():
    results = [
        {"digest": "sha256:0123456789abcdef", "owner": "sam",
         "repo": "github.com/sam/nethacker", "commit_sha": "a" * 40,
         "registered_at": "2026-01-01T00:00:00Z"}
    ]
    out = _render_text(rich_search(results))
    assert "solution" in out and "owner" in out and "repo" in out
    assert "sam" in out
    assert "github.com/sam/nethacker" in out
    assert _short_digest(results[0]["digest"]) in out
    assert _short_digest(results[0]["commit_sha"]) in out


def test_rich_render_search_empty_is_friendly_not_bare_header():
    assert _render_text(rich_search([])).strip() == "no solutions found."


def test_rich_render_show_contains_expected_cells():
    full_digest = "sha256:" + ("0123456789abcdef" * 2)
    solution = {
        "digest": full_digest, "repo": "github.com/sam/nethacker", "commit_sha": "b" * 40,
        "owner": "sam", "root": ".", "entrypoint": "bot.py",
        "registered_at": "2026-01-01T00:00:00Z",
    }
    out = _render_text(rich_show(solution))
    assert "digest" in out
    assert "owner" in out and "sam" in out
    assert _short_digest(full_digest) in out
    assert full_digest.removeprefix("sha256:") not in out  # actually shortened


def test_rich_render_show_empty_is_friendly_not_bare_block():
    assert _render_text(rich_show({})).strip() == "no such solution."


def test_rich_renderers_hyperlink_owner_and_repo_to_github():
    # OSC-8 hyperlinks are only emitted to a real terminal, so force one; the
    # URL then appears in the raw output. (Terminals without hyperlink support
    # just show the plain name; -o json is untouched -- see the json tests.)
    def term(renderable):
        buf = io.StringIO()
        Console(file=buf, force_terminal=True, width=140).print(renderable)
        return buf.getvalue()

    board = term(rich_board([
        {"rank": 1, "solution_digest": "sha256:abc", "owner": "octocat",
         "ascensions": 1, "median_progression": 0.5, "mean_progression": 0.5}
    ]))
    assert "@ octocat" in board  # displayed GitHub-style with a space
    assert "https://github.com/octocat" in board  # owner -> profile (link target has no @)

    search = term(rich_search([
        {"digest": "sha256:abc", "owner": "octocat",
         "repo": "github.com/octocat/nethacker", "commit_sha": "a" * 40,
         "registered_at": "2026-01-01T00:00:00Z"}
    ]))
    assert "https://github.com/octocat" in search  # owner
    assert "https://github.com/octocat/nethacker" in search  # repo
    assert "https://github.com/octocat/nethacker/commit/" + "a" * 40 in search  # commit


def test_ramp_clamps_and_interpolates():
    assert ramp(0.0) == "rgb(68,1,84)"
    assert ramp(1.0) == "rgb(253,231,37)"
    assert ramp(-5.0) == ramp(0.0)  # clamped below
    assert ramp(5.0) == ramp(1.0)  # clamped above
    mid = ramp(0.5)
    assert mid.startswith("rgb(") and mid.endswith(")")
    assert mid not in (ramp(0.0), ramp(1.0))  # a real interpolation, not just a clamp


# CLI-level table smoke tests: prove the wiring (args.output -> emit(...,
# table=rich_*)) actually reaches the console, not just the renderers
# in isolation.


def test_cli_board_output_table_renders_through_console(monkeypatch, capsys):
    response = [
        {"rank": 1, "solution_digest": "sha256:0123456789abcdef", "owner": "sam",
         "cells_held": 3}
    ]
    FakeHubClient, _calls = _make_fake_hub_client({"board": response})
    monkeypatch.setattr(C, "HubClient", FakeHubClient)

    rc = C.main(["board", "--metric", "coverage", "-o", "table"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "sam" in out
    assert "cells" in out


def test_cli_map_output_table_renders_universe_grid_through_console(monkeypatch, capsys):
    rows = [
        {"identity": "val-hum-neu-fem", "solution_digest": "sha256:a", "score": 0.42, "rank": 1},
        {"identity": "wiz-elf-cha-mal", "solution_digest": "sha256:b", "score": 0.77, "rank": 1},
    ]
    FakeHubClient, _calls = _make_fake_hub_client({"elites": rows})
    monkeypatch.setattr(C, "HubClient", FakeHubClient)
    monkeypatch.setattr(O.console, "_width", 200)  # wide: no truncation

    rc = C.main(["map", "-o", "table"])

    assert rc == 0
    out = capsys.readouterr().out
    # a number grid keyed by role name now, not a per-identity progress table
    assert "Valkyrie" in out and "Wizard" in out
    assert "hum-neu-fem" in out and "elf-cha-mal" in out  # variation labels
    assert "0.42" in out and "0.77" in out  # the known identity numbers


def test_cli_auto_resolves_to_table_under_forced_terminal(monkeypatch, capsys):
    # No -o given at all -- the CLI's own top-level default ("auto") must
    # resolve to a rich table (not JSON) once stdout looks like a terminal.
    monkeypatch.setattr(O.console, "_force_terminal", True)
    response = [
        {"rank": 1, "solution_digest": "sha256:0123456789abcdef", "owner": "sam",
         "cells_held": 3}
    ]
    FakeHubClient, _calls = _make_fake_hub_client({"board": response})
    monkeypatch.setattr(C, "HubClient", FakeHubClient)

    rc = C.main(["board", "--metric", "coverage"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "sam" in out
    with pytest.raises(json.JSONDecodeError):
        json.loads(out)  # proves the table path was taken, not the json fallback


# --- Group 4: -o plain reproduces the baseline pure-Python tables ----------
# (These bodies are the pre-CLI-UX-pass "renders ascii table by default"
# tests -- the default changed (group 1), but the plain renders themselves
# didn't, so -o plain must still produce exactly what they used to.)


def test_cli_map_output_plain_matches_baseline_frontier(monkeypatch, capsys):
    rows = [
        {"identity": "val-hum-neu-fem", "solution_digest": "sha256:a", "score": 0.42, "rank": 1},
    ]
    FakeHubClient, _calls = _make_fake_hub_client({"elites": rows})
    monkeypatch.setattr(C, "HubClient", FakeHubClient)

    rc = C.main(["map", "-o", "plain"])

    assert rc == 0
    out = capsys.readouterr().out
    assert out == plain_frontier({"val-hum-neu-fem": 0.42}) + "\n"  # byte-for-byte baseline
    assert "val-hum-neu-fem" in out
    assert "0.42" in out


def test_cli_map_output_plain_on_empty_response_prints_friendly_message(monkeypatch, capsys):
    FakeHubClient, _calls = _make_fake_hub_client({"elites": []})
    monkeypatch.setattr(C, "HubClient", FakeHubClient)

    rc = C.main(["map", "-o", "plain"])

    assert rc == 0
    assert capsys.readouterr().out.strip() == "no frontier data yet."


def test_cli_board_output_plain_matches_baseline_table(monkeypatch, capsys):
    response = [
        {
            "rank": 1,
            "solution_digest": "sha256:0123456789abcdef",
            "owner": "sam",
            "episodes": 8,
            "ascensions": 1,
            "median_progression": 0.5,
            "mean_progression": 0.4,
        }
    ]
    FakeHubClient, _calls = _make_fake_hub_client({"board": response})
    monkeypatch.setattr(C, "HubClient", FakeHubClient)

    rc = C.main(["board", "--objective", "random", "-o", "plain"])

    assert rc == 0
    out = capsys.readouterr().out
    assert out == plain_board(response) + "\n"
    assert "1" in out
    assert "sam" in out
    assert _short_digest(response[0]["solution_digest"]) in out
    # Old papercut #3: naive `str(digest)[:12]` collapses onto the shared
    # "sha256:" prefix -- the fixed render must not show that collision.
    assert response[0]["solution_digest"][:12] not in out


def test_cli_board_output_plain_tolerates_coverage_shape_without_crashing(monkeypatch, capsys):
    # coverage/firsts board entries carry fewer columns than a grading
    # board entry (no ascensions/median_progression/mean_progression).
    response = [
        {"rank": 1, "solution_digest": "sha256:0123456789abcdef", "owner": "sam",
         "cells_held": 3}
    ]
    FakeHubClient, _calls = _make_fake_hub_client({"board": response})
    monkeypatch.setattr(C, "HubClient", FakeHubClient)

    rc = C.main(["board", "--metric", "coverage", "-o", "plain"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "sam" in out
    assert "cells" in out  # the shape-aware count column header
    # Old papercut #2: the count column rendered blank instead of "3".
    assert "3" in out


def test_cli_elites_output_plain_matches_baseline_table(monkeypatch, capsys):
    response = [
        {
            "identity": "val-dwa-law-fem",
            "solution_digest": "sha256:0123456789abcdef",
            "score": 0.5087697678994835,
            "rank": 1,
        }
    ]
    FakeHubClient, _calls = _make_fake_hub_client({"elites": response})
    monkeypatch.setattr(C, "HubClient", FakeHubClient)

    rc = C.main(["elites", "--objective", "val-dwa-law-fem", "-o", "plain"])

    assert rc == 0
    out = capsys.readouterr().out
    assert out == plain_elites(response) + "\n"
    assert "identity" in out and "score" in out  # header
    assert "val-dwa-law-fem" in out
    assert _short_digest(response[0]["solution_digest"]) in out
    assert "0.509" in out  # rounded
    assert "0.5087697678994835" not in out  # not full precision


def test_cli_search_output_plain_matches_baseline_table(monkeypatch, capsys):
    response = [
        {
            "digest": "sha256:0123456789abcdef",
            "owner": "sam",
            "repo": "github.com/sam/nethacker",
            "commit_sha": "a" * 40,
            "root": ".",
            "entrypoint": "bot.py",
            "registered_at": "2026-01-01T00:00:00Z",
        }
    ]
    FakeHubClient, _calls = _make_fake_hub_client({"search": response})
    monkeypatch.setattr(C, "HubClient", FakeHubClient)

    rc = C.main(["search", "-o", "plain"])

    assert rc == 0
    out = capsys.readouterr().out
    assert out == plain_search(response) + "\n"
    assert "solution" in out and "owner" in out and "repo" in out  # header
    assert "sam" in out
    assert "github.com/sam/nethacker" in out
    assert _short_digest(response[0]["digest"]) in out
    assert _short_digest(response[0]["commit_sha"]) in out


def test_cli_show_output_plain_matches_baseline_table(monkeypatch, capsys):
    full_digest = "sha256:" + ("0123456789abcdef" * 2)  # 32 hex chars after the prefix
    response = {
        "digest": full_digest,
        "repo": "github.com/sam/nethacker",
        "commit_sha": "b" * 40,
        "owner": "sam",
        "root": ".",
        "entrypoint": "bot.py",
        "registered_at": "2026-01-01T00:00:00Z",
    }
    FakeHubClient, _calls = _make_fake_hub_client({"show": response})
    monkeypatch.setattr(C, "HubClient", FakeHubClient)

    rc = C.main(["show", full_digest, "-o", "plain"])

    assert rc == 0
    out = capsys.readouterr().out
    assert out == plain_show(response) + "\n"
    assert "digest" in out
    assert "owner" in out and "sam" in out
    assert _short_digest(full_digest) in out
    # proves it's actually shortened, not just echoed verbatim
    assert full_digest.removeprefix("sha256:") not in out


# --- Property 5: --hub default + override, before AND after the subcommand -


def test_cli_hub_defaults_to_localhost(monkeypatch):
    FakeHubClient, calls = _make_fake_hub_client({})
    monkeypatch.setattr(C, "HubClient", FakeHubClient)
    monkeypatch.delenv("NETHACKERS_HUB", raising=False)

    rc = C.main(["map"])

    assert rc == 0
    assert calls[0] == ("__init__", "http://localhost:8000")


def test_cli_hub_override_flag(monkeypatch):
    FakeHubClient, calls = _make_fake_hub_client({})
    monkeypatch.setattr(C, "HubClient", FakeHubClient)

    rc = C.main(["--hub", "http://example.com:9000", "map"])

    assert rc == 0
    assert calls[0] == ("__init__", "http://example.com:9000")


def test_cli_hub_env_var_default(monkeypatch):
    FakeHubClient, calls = _make_fake_hub_client({})
    monkeypatch.setattr(C, "HubClient", FakeHubClient)
    monkeypatch.setenv("NETHACKERS_HUB", "http://env-hub:1234")

    rc = C.main(["map"])

    assert rc == 0
    assert calls[0] == ("__init__", "http://env-hub:1234")


def test_cli_hub_flag_after_subcommand(monkeypatch):
    # Final-review fix B: --hub must work AFTER the subcommand too, not
    # just before it.
    FakeHubClient, calls = _make_fake_hub_client({"board": []})
    monkeypatch.setattr(C, "HubClient", FakeHubClient)

    rc = C.main(["board", "--objective", "random", "--hub", "http://example.com:9000"])

    assert rc == 0
    assert calls[0] == ("__init__", "http://example.com:9000")
    assert ("board", "random", None) in calls


# --- Group 5: human chrome (register's device flow, errors) -> stderr -----


def test_cli_register_device_flow_prompt_goes_to_stderr_not_stdout(monkeypatch, capsys, tmp_path):
    solution_dir = tmp_path / "solution"
    solution_dir.mkdir()
    manifest = {"root": ".", "entrypoint": "bot.py"}
    (solution_dir / "nethackers.solution.json").write_text(json.dumps(manifest))
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(json.dumps({"solution_digest": "sha256:abc"}))

    FakeHubClient, _calls = _make_fake_hub_client({})
    monkeypatch.setattr(C, "HubClient", FakeHubClient)

    def fake_register_solution(*, hub, reference, manifest, evidence, prompt=print):
        prompt(
            "To authorize, open https://github.com/login/device and enter code: ABCD-1234"
        )
        return {"solution_digest": "sha256:abc", "owner": "sam"}

    monkeypatch.setattr(C, "register_solution", fake_register_solution)

    rc = C.main(
        [
            "register",
            "--repo", "github.com/sam/nethacker",
            "--commit", "a" * 40,
            "--solution", str(solution_dir),
            "--evidence", str(evidence_path),
            "-o", "json",
        ]
    )

    assert rc == 0
    captured = capsys.readouterr()
    assert "To authorize" in captured.err
    assert "To authorize" not in captured.out
    # -o json mode: stdout carries ONLY the final JSON result, nothing else.
    assert json.loads(captured.out) == {"solution_digest": "sha256:abc", "owner": "sam"}


def test_cli_eval_unknown_objective_error_goes_to_stderr_via_rich_console(capsys, tmp_path):
    # Same contract as tests/test_cli.py's coverage of this path, but here
    # specifically pinning that it now flows through hubclient.output.err
    # (a rich Console) rather than a bare print(..., file=sys.stderr) --
    # both land on real stderr, so this must keep passing either way.
    rc = C.main(["eval", str(tmp_path), "--objective", "not-a-real-objective"])

    assert rc == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "not-a-real-objective" in captured.err


# --- Property 6: the shared table/digest/rounding helpers + empty renders --


def test_table_helper_right_aligns_pure_numeric_column():
    out = _table(["n"], [["3"], ["12"]])
    lines = out.splitlines()
    assert lines[2] == " 3"  # padded on the LEFT to match "12"'s width
    assert lines[3] == "12"


def test_table_helper_left_aligns_text_right_aligns_numeric_in_same_table():
    out = _table(["name", "x"], [["bob", "1"], ["alice", "2"]])
    lines = out.splitlines()
    # left-aligned text column: short values padded on the right
    assert lines[2][:5] == "bob  "
    assert lines[3][:5] == "alice"
    # right-aligned numeric column: values line up at the end of the line
    assert lines[2].endswith("1")
    assert lines[3].endswith("2")


def test_table_helper_never_crashes_on_empty_rows():
    out = _table(["a", "b"], [])
    lines = out.splitlines()
    assert len(lines) == 2  # header + rule, no data rows
    assert "a" in lines[0]
    assert "b" in lines[0]


def test_short_digest_strips_prefix_and_distinguishes_similar_digests():
    a = _short_digest("sha256:0123456789abcdef")
    b = _short_digest("sha256:0123456789fedcba")
    assert not a.startswith("sha256:")
    assert a == "0123456789ab"
    assert b == "0123456789fe"
    assert a != b  # the two must render distinguishably, not both "sha256:012"


def test_num_rounds_floats_and_passes_non_floats_through():
    assert _num(0.5087697678994835) == "0.509"  # rounded, not full precision
    assert _num(3) == "3"
    assert _num(0.4) == "0.400"
    assert _num(None) == "None"


def test_plain_render_elites_empty_is_friendly_not_bare_header():
    assert plain_elites([]) == "no elites recorded yet."


def test_plain_render_board_empty_is_friendly_not_bare_header():
    assert plain_board([]) == "no board entries yet."


def test_plain_render_search_empty_is_friendly_not_bare_header():
    assert plain_search([]) == "no solutions found."


def test_plain_render_show_empty_is_friendly_not_bare_header():
    assert plain_show({}) == "no such solution."


# --- Property 7: the loop-closing regression --------------------------------
# The final review's blocker: CLI `eval --objective` must produce evidence
# that `register` actually accepts. Before this fix, `eval` had no
# `--objective` flag at all and (via the retired legacy path) always
# produced `objective.seed_set="cli"`, which `register` always rejected with
# UnknownObjective -- this test fails against that pre-fix CLI.


def test_cli_eval_objective_produces_registerable_evidence(monkeypatch, capsys, tmp_path):
    identity = "val-dwa-law-fem"
    spec = CATALOG[identity]

    def fake_eval_batch(solution, spec_arg, image, *, now, max_parallel_evals=8):
        assert spec_arg is spec  # the CLI resolved exactly this catalog entry
        results = [
            TrajectoryResult(
                trajectory_id=seed, status="completed", progress=0.1, ascended=False,
                steps=10, turns=9, max_depth=2, end_status="died", error=None,
                wall_seconds=0.1, character=character, milestone=None,
            )
            for seed, character in spec.batch
        ]
        objective = Objective(
            character=None, max_steps=spec.max_steps,
            no_progress_timeout=spec.no_progress_timeout,
            action_timeout_seconds=spec.action_timeout_seconds, seed_set=spec.name,
        )
        return Evidence.from_results(
            solution_digest="sha256:looptest", objective=objective,
            evaluator_image=image, results=results, created_at=now,
        )

    monkeypatch.setattr(C, "eval_batch", fake_eval_batch)

    rc = C.main(["eval", str(tmp_path), "--objective", identity, "--image", "img:dev"])
    assert rc == 0
    evidence = Evidence.from_dict(json.loads(capsys.readouterr().out))

    store = Store(tmp_path / "hub.sqlite3")
    store.init_schema()
    auth = LocalStubAuth({"tok-sam": "sam"})
    git = LocalStubGit(
        manifest={"root": ".", "entrypoint": "bot.py"}, digest=evidence.solution_digest
    )
    reference = SolutionReference(repo="github.com/sam/nethacker", commit="a" * 40)

    result = register(
        store, auth, token="tok-sam", reference=reference, evidence=evidence,
        git=git, now="2026-01-01T00:00:00Z",
    )

    assert result.objective == identity
    assert result.atoms_inserted == len(spec.batch)
