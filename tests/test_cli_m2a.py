"""Tests for the M2a hub-facing CLI subcommands added to ``nethackers.cli``
in Task 13: ``map``/``attainment``, ``elites``, ``board``, ``search``,
``show``, ``register``, plus the global ``--hub`` option. ``HubClient`` and
``register_solution`` are monkeypatched on the ``cli`` module throughout --
no real HTTP/network call is ever made, mirroring how ``tests/test_cli.py``
monkeypatches ``eval_batch``/``pull`` for the pre-existing subcommands. That
file's ``eval``/``pull`` coverage is untouched and must stay green alongside
this one.

Final-review fix additions (folding in a CLI-UX pass -- see
fix-final-review-context.md): ``--hub``/``--json`` on a shared parent
parser (works before OR after the subcommand), a beautified default render
for every read subcommand via ``hubclient.client``'s ``_table``/
``_short_digest``/``_num`` helpers, and the loop-closing regression proving
CLI ``eval --objective`` evidence is actually registerable (the exact gap
the final review found).
"""

from __future__ import annotations

import json

import nethackers.cli as C
from nethackers.contracts.models import Evidence, Objective, TrajectoryResult
from nethackers.hub.auth import LocalStubAuth
from nethackers.hub.objectives import CATALOG
from nethackers.hub.store import Store
from nethackers.hub.validate import LocalStubGit, SolutionReference, register
from nethackers.hubclient.client import (
    _num,
    _short_digest,
    _table,
    render_board,
    render_elites,
    render_search,
    render_show,
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

        def attainment(self, identity=None):
            calls.append(("attainment", identity))
            return response_map.get("attainment", [])

        def elites(self, objective):
            calls.append(("elites", objective))
            return response_map.get("elites", [])

        def board(self, objective=None, metric=None):
            calls.append(("board", objective, metric))
            return response_map.get("board", [])

        def search(self, owner=None, limit=50, offset=0):
            calls.append(("search", owner, limit, offset))
            return response_map.get("search", [])

        def show(self, digest):
            calls.append(("show", digest))
            return response_map.get("show", {})

    return FakeHubClient, calls


# --- Property 3: each subcommand dispatches to the right client method ----


def test_cli_map_dispatches_to_attainment(monkeypatch, capsys):
    FakeHubClient, calls = _make_fake_hub_client({"attainment": []})
    monkeypatch.setattr(C, "HubClient", FakeHubClient)

    rc = C.main(["map", "--identity", "val-dwa-law-fem"])

    assert rc == 0
    assert ("attainment", "val-dwa-law-fem") in calls


def test_cli_attainment_alias_dispatches_to_attainment(monkeypatch, capsys):
    FakeHubClient, calls = _make_fake_hub_client({"attainment": []})
    monkeypatch.setattr(C, "HubClient", FakeHubClient)

    rc = C.main(["attainment"])

    assert rc == 0
    assert ("attainment", None) in calls


def test_cli_elites_dispatches_with_objective(monkeypatch, capsys):
    FakeHubClient, calls = _make_fake_hub_client({"elites": [{"identity": "x"}]})
    monkeypatch.setattr(C, "HubClient", FakeHubClient)

    rc = C.main(["elites", "--objective", "random", "--json"])

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

    rc = C.main(["search", "--owner", "sam", "--limit", "10", "--offset", "5", "--json"])

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

    rc = C.main(["show", "sha256:abc", "--json"])

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

    def fake_register_solution(*, hub, reference, manifest, evidence):
        seen["hub"] = hub
        seen["reference"] = reference
        seen["manifest"] = manifest
        seen["evidence"] = evidence
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

    def fake_register_solution(*, hub, reference, manifest, evidence):
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


# --- Property 4: map/board render a beautified table by default ------------


def test_cli_map_renders_ascii_table(monkeypatch, capsys):
    response = [
        {
            "identity": "val-dwa-law-fem",
            "milestone": "Dlvl:3",
            "first_solution": "sha256:abc",
            "first_owner": "sam",
            "first_at": "2026-01-01T00:00:00Z",
            "holder_count": 2,
        }
    ]
    FakeHubClient, _calls = _make_fake_hub_client({"attainment": response})
    monkeypatch.setattr(C, "HubClient", FakeHubClient)

    rc = C.main(["map"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "val-dwa-law-fem" in out
    assert "Dlvl:3" in out
    assert "sam" in out


def test_cli_map_empty_response_prints_friendly_message_not_crash(monkeypatch, capsys):
    FakeHubClient, _calls = _make_fake_hub_client({"attainment": []})
    monkeypatch.setattr(C, "HubClient", FakeHubClient)

    rc = C.main(["map"])

    assert rc == 0
    out = capsys.readouterr().out.strip()
    assert out == "no attainment cells yet."


def test_cli_map_json_on_empty_response_emits_empty_json_array(monkeypatch, capsys):
    # --json bypasses the friendly-message default entirely -- it always
    # prints exactly the raw hub response, empty or not.
    FakeHubClient, _calls = _make_fake_hub_client({"attainment": []})
    monkeypatch.setattr(C, "HubClient", FakeHubClient)

    rc = C.main(["map", "--json"])

    assert rc == 0
    assert json.loads(capsys.readouterr().out) == []


def test_cli_board_renders_ascii_table(monkeypatch, capsys):
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

    rc = C.main(["board", "--objective", "random"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "1" in out
    assert "sam" in out
    assert _short_digest(response[0]["solution_digest"]) in out
    # Old papercut #3: naive `str(digest)[:12]` collapses onto the shared
    # "sha256:" prefix -- the fixed render must not show that collision.
    assert response[0]["solution_digest"][:12] not in out


def test_cli_board_tolerates_coverage_shape_without_crashing(monkeypatch, capsys):
    # coverage/firsts board entries carry fewer columns than a grading
    # board entry (no ascensions/median_progression/mean_progression).
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
    assert "cells" in out  # the shape-aware count column header
    # Old papercut #2: the count column rendered blank instead of "3".
    assert "3" in out


def test_cli_board_json_emits_raw_response_equal_to_stub(monkeypatch, capsys):
    response = [
        {"rank": 1, "solution_digest": "sha256:0123456789abcdef", "owner": "sam",
         "episodes": 8, "ascensions": 1, "median_progression": 0.5, "mean_progression": 0.4}
    ]
    FakeHubClient, _calls = _make_fake_hub_client({"board": response})
    monkeypatch.setattr(C, "HubClient", FakeHubClient)

    rc = C.main(["board", "--objective", "random", "--json"])

    assert rc == 0
    assert json.loads(capsys.readouterr().out) == response


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


# --- Property 6: beautified renders + --json (CLI-UX pass) -----------------


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


def test_render_elites_empty_is_friendly_not_bare_header():
    assert render_elites([]) == "no elites recorded yet."


def test_render_board_empty_is_friendly_not_bare_header():
    assert render_board([]) == "no board entries yet."


def test_render_search_empty_is_friendly_not_bare_header():
    assert render_search([]) == "no solutions found."


def test_render_show_empty_is_friendly_not_bare_header():
    assert render_show({}) == "no such solution."


def test_cli_elites_renders_table_by_default(monkeypatch, capsys):
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

    rc = C.main(["elites", "--objective", "val-dwa-law-fem"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "identity" in out and "score" in out  # header
    assert "val-dwa-law-fem" in out
    assert _short_digest(response[0]["solution_digest"]) in out
    assert "0.509" in out  # rounded
    assert "0.5087697678994835" not in out  # not full precision


def test_cli_search_renders_table_by_default(monkeypatch, capsys):
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

    rc = C.main(["search"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "solution" in out and "owner" in out and "repo" in out  # header
    assert "sam" in out
    assert "github.com/sam/nethacker" in out
    assert _short_digest(response[0]["digest"]) in out
    assert _short_digest(response[0]["commit_sha"]) in out


def test_cli_show_renders_key_value_by_default(monkeypatch, capsys):
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

    rc = C.main(["show", full_digest])

    assert rc == 0
    out = capsys.readouterr().out
    assert "digest" in out
    assert "owner" in out and "sam" in out
    assert _short_digest(full_digest) in out
    # proves it's actually shortened, not just echoed verbatim
    assert full_digest.removeprefix("sha256:") not in out


# --- Property 7: the loop-closing regression --------------------------------
# The final review's blocker: CLI `eval --objective` must produce evidence
# that `register` actually accepts. Before this fix, `eval` had no
# `--objective` flag at all and (via the retired legacy path) always
# produced `objective.seed_set="cli"`, which `register` always rejected with
# UnknownObjective -- this test fails against that pre-fix CLI.


def test_cli_eval_objective_produces_registerable_evidence(monkeypatch, capsys, tmp_path):
    identity = "val-dwa-law-fem"
    spec = CATALOG[identity]

    def fake_eval_batch(solution, spec_arg, image, *, now):
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
