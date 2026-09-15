"""Locks the `nethackers doctor -o json` contract (spec docs/superpowers/specs/
2026-08-28-sandbox-image-distribution-design.md S5.6/INV6) against the
committed ``src/nethackers/doctor.schema.json``.

Two independent guarantees, deliberately kept apart:

- **Drift gate** (``test_committed_schema_matches_check_specs``): builds the
  schema FRESH from ``CHECK_SPECS``/``CAPABILITIES`` (the single source for
  every check's id/severity/capabilities -- see ``diagnostics.py``) and
  asserts it deep-equals the committed file. This function is NOT imported
  from production code on purpose -- ``doctor.schema.json`` is a hand-
  committed artifact, not something ``to_json`` generates at runtime, so if
  a future change to ``CHECK_SPECS`` (a new check id, a re-tagged
  capability, a new severity) isn't ALSO mirrored into the committed JSON
  file, this test fails instead of the contract silently drifting out from
  under whoever consumes it (a bug-report parser, a future TUI panel).
- **Conformance** (``test_to_json_output_conforms_to_schema``): every shape
  ``to_json`` can actually produce (built via real ``run_checks`` calls with
  injected fakes, never real docker/network) validates against that exact
  committed file with ``jsonschema.Draft202012Validator`` -- so the schema
  isn't just internally self-consistent, it actually describes what the
  code emits.
"""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from nethackers.containers import RuntimeCandidate, RuntimeReport
from nethackers.diagnostics import CAPABILITIES, CHECK_SPECS, run_checks, to_json
from nethackers.hubclient.client import HubUnreachable
from nethackers.hubclient.credentials import Credentials

_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "src" / "nethackers" / "doctor.schema.json"


def _runtime_ok():
    return RuntimeReport("docker", (RuntimeCandidate("docker", "usable", ""),))


def _runtime_none():
    return RuntimeReport(None, (RuntimeCandidate("docker", "absent", ""),
                               RuntimeCandidate("podman", "absent", "")))


def _load_schema() -> dict:
    """The committed contract file -- the one small loader helper every test
    below shares, so there's exactly one place that names the path."""
    return json.loads(_SCHEMA_PATH.read_text())


# --- drift gate: CHECK_SPECS/CAPABILITIES -> schema, compared to the file --


def _expected_schema() -> dict:
    """Rebuilds the schema dict-for-dict from the CURRENT ``CHECK_SPECS``/
    ``CAPABILITIES`` in this checkout. Deliberately duplicates the shape of
    ``doctor.schema.json`` by hand (rather than sharing a builder function
    with anything under ``src/``) -- the whole point of a drift gate is that
    the enum lists here are re-derived from the check-model's *current*
    state every test run, while the committed file only changes when a
    human consciously regenerates and commits it.
    """
    check_ids = sorted(CHECK_SPECS)
    severities = sorted({severity for severity, _caps in CHECK_SPECS.values()})
    caps = list(CAPABILITIES)
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://nethackers.dunnolab.ai/schemas/doctor.schema.json",
        "title": "nethackers doctor -o json",
        "description": (
            "Complete bug-report shape for `nethackers doctor -o json` (design spec "
            "docs/superpowers/specs/2026-08-28-sandbox-image-distribution-design.md S5.6). "
            "checks[].id / checks[].severity / checks[].capabilities are a closed enum "
            "derived from nethackers.diagnostics.CHECK_SPECS -- any change to that table "
            "must be mirrored here, or tests/test_doctor_schema.py's drift gate fails."
        ),
        "type": "object",
        "additionalProperties": False,
        "required": ["checks", "capabilities", "env"],
        "properties": {
            "checks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["id", "status", "severity", "detail", "fix", "capabilities"],
                    "properties": {
                        "id": {"type": "string", "enum": check_ids},
                        "status": {"type": "string", "enum": ["ok", "warn", "fail"]},
                        "severity": {"type": "string", "enum": severities},
                        "detail": {"type": "string"},
                        "fix": {"type": ["string", "null"]},
                        "capabilities": {
                            "type": "array",
                            "items": {"type": "string", "enum": caps},
                        },
                    },
                },
            },
            "capabilities": {
                "type": "object",
                "additionalProperties": False,
                "required": caps,
                "properties": {cap: {"type": "boolean"} for cap in caps},
            },
            "env": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "nethackers", "run_schema_version", "images", "os", "arch", "python",
                ],
                "properties": {
                    "nethackers": {"type": "string"},
                    "run_schema_version": {"type": "string"},
                    "images": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["arena", "mutator"],
                        "properties": {
                            "arena": {"type": "string"},
                            "mutator": {"type": "string"},
                        },
                    },
                    "os": {"type": "string"},
                    "arch": {"type": "string"},
                    "python": {"type": "string"},
                },
            },
        },
    }


def test_committed_schema_matches_check_specs():
    # Belt-and-suspenders: the committed file must itself be a syntactically
    # valid 2020-12 schema, not just byte-equal to something we happen to
    # both compute the same (fully offline -- jsonschema vendors the draft
    # meta-schemas, no network fetch).
    committed = _load_schema()
    jsonschema.Draft202012Validator.check_schema(committed)

    assert committed == _expected_schema(), (
        "doctor.schema.json has drifted from CHECK_SPECS/CAPABILITIES -- regenerate it "
        "(see _expected_schema() in this file) and commit the update alongside the "
        "diagnostics.py change that caused the drift"
    )


# --- conformance: real to_json(...) outputs validate against the committed file


_HEX64 = "b" * 64


def _ref(explicit, kind):
    return f"ghcr.io/dunnolab/nethackers-{kind}@sha256:{_HEX64}"


def _unreachable_hub(hub):
    raise HubUnreachable(hub)


def _kwargs(**overrides):
    # Mirrors test_diagnostics.py's `_healthy_kwargs` (kept as an independent
    # copy here on purpose -- this file's own convention, matching
    # test_doctor_cli.py, is that each test module owns its fakes rather
    # than importing helpers from a sibling test module).
    kwargs = dict(
        operator="claude",
        hub="https://example.invalid",
        runtime_report=_runtime_ok,
        resolve_image=_ref,
        image_present=lambda ref: True,
        manifest_reachable=lambda ref: True,
        repo_root=lambda: None,
        preflight_operator=lambda operator: None,
        hub_mode=lambda hub: "github",
        load_creds=lambda: Credentials("castiel", "tok"),
        gh_state=lambda: ("castiel", "authed"),
        # Without this override, the real default reads the actual host's
        # Docker Desktop settings file -- breaking run_checks's own
        # documented "no real...call is made by this function's own test
        # suite" guarantee (test_diagnostics.py's `_healthy_kwargs` carries
        # the identical fake, for the identical reason).
        rosetta=lambda: ("ok", "Rosetta is accelerating amd64 emulation"),
    )
    kwargs.update(overrides)
    return kwargs


def _all_ok_json() -> dict:
    return to_json(run_checks(**_kwargs()))


def _all_down_json() -> dict:
    # Every probe fails/unreachable -- exercises "fail" across every check.
    return to_json(run_checks(**_kwargs(
        runtime_report=_runtime_none,
        image_present=lambda ref: False,
        manifest_reachable=lambda ref: False,
        preflight_operator=lambda operator: "not logged in",
        hub_mode=_unreachable_hub,
        load_creds=lambda: None,
        gh_state=lambda: (None, "missing"),
    )))


def _gh_unauthed_json() -> dict:
    # The single most-forgotten setup step (spec S5.6) -- hub login present,
    # gh installed but not authed: a realistic partial-failure shape.
    return to_json(run_checks(**_kwargs(gh_state=lambda: (None, "unauthed"))))


def _image_pullable_json() -> dict:
    # Not local yet, but the registry has it -- the one scenario that
    # actually produces a "warn" status, so the corpus exercises every
    # member of the status enum at least once, not just ok/fail.
    return to_json(run_checks(**_kwargs(
        image_present=lambda ref: False, manifest_reachable=lambda ref: True,
    )))


@pytest.mark.parametrize(
    "build",
    [_all_ok_json, _all_down_json, _gh_unauthed_json, _image_pullable_json],
    ids=["all_ok", "all_down", "gh_unauthed", "image_pullable"],
)
def test_to_json_output_conforms_to_schema(build):
    schema = _load_schema()
    instance = build()

    errors = list(jsonschema.Draft202012Validator(schema).iter_errors(instance))

    assert errors == [], "\n".join(str(e) for e in errors)
