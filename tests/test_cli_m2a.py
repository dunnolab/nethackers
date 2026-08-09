"""Tests for the M2a hub-facing CLI subcommands added to ``nethackers.cli``
in Task 13: ``map``/``attainment``, ``elites``, ``board``, ``search``,
``show``, ``register``, plus the global ``--hub`` option. ``HubClient`` and
``register_solution`` are monkeypatched on the ``cli`` module throughout --
no real HTTP/network call is ever made, mirroring how ``tests/test_cli.py``
monkeypatches ``run_eval``/``pull`` for the pre-existing subcommands. That
file's ``eval``/``pull`` coverage is untouched and must stay green alongside
this one.
"""

from __future__ import annotations

import json

import nethackers.cli as C


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

    rc = C.main(["elites", "--objective", "random"])

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

    rc = C.main(["search", "--owner", "sam", "--limit", "10", "--offset", "5"])

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

    rc = C.main(["show", "sha256:abc"])

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


# --- Property 4: map/board render ASCII (key rows present) -----------------


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


def test_cli_map_empty_response_prints_header_not_crash(monkeypatch, capsys):
    FakeHubClient, _calls = _make_fake_hub_client({"attainment": []})
    monkeypatch.setattr(C, "HubClient", FakeHubClient)

    rc = C.main(["map"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "identity" in out  # header row still printed


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
    assert response[0]["solution_digest"][:12] in out


def test_cli_board_tolerates_coverage_shape_without_crashing(monkeypatch, capsys):
    # coverage/firsts board entries carry fewer columns than a grading
    # board entry (no ascensions/median_progression/mean_progression).
    response = [{"rank": 1, "solution_digest": "sha256:0123456789abcdef", "owner": "sam",
                 "cells_held": 3}]
    FakeHubClient, _calls = _make_fake_hub_client({"board": response})
    monkeypatch.setattr(C, "HubClient", FakeHubClient)

    rc = C.main(["board", "--metric", "coverage"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "sam" in out


# --- Property 5: --hub default + override -----------------------------------


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
