import json
from pathlib import Path

import pytest

from nethackers.diagnostics import CHECK_SPECS, _check_rosetta, exit_code, rosetta_state

SETTINGS = "settings-store.json"


def _settings(tmp_path: Path, **keys) -> Path:
    path = tmp_path / SETTINGS
    path.write_text(json.dumps(keys))
    return path


def test_rosetta_on_reports_ok(tmp_path):
    path = _settings(
        tmp_path,
        UseVirtualizationFramework=True,
        UseVirtualizationFrameworkRosetta=True,
    )
    status, detail = rosetta_state("Darwin", "arm64", path)
    assert status == "ok"
    assert "Rosetta" in detail


def test_rosetta_off_reports_warn_with_the_measured_cost(tmp_path):
    path = _settings(
        tmp_path,
        UseVirtualizationFramework=False,
        UseVirtualizationFrameworkRosetta=False,
    )
    status, detail = rosetta_state("Darwin", "arm64", path)
    assert status == "warn"
    assert "823" in detail and "224" in detail


def test_virtualization_framework_off_is_a_warn_because_rosetta_needs_it(tmp_path):
    path = _settings(
        tmp_path,
        UseVirtualizationFramework=False,
        UseVirtualizationFrameworkRosetta=True,
    )
    assert rosetta_state("Darwin", "arm64", path)[0] == "warn"


def test_unreadable_settings_reports_unknown_never_disabled(tmp_path):
    status, detail = rosetta_state("Darwin", "arm64", tmp_path / "absent.json")
    assert status == "unknown"
    assert "Colima" in detail or "colima" in detail


def test_intel_mac_is_unknown_because_it_emulates_nothing(tmp_path):
    assert rosetta_state("Darwin", "x86_64", _settings(tmp_path))[0] == "unknown"


def test_linux_is_unknown(tmp_path):
    assert rosetta_state("Linux", "x86_64", _settings(tmp_path))[0] == "unknown"


def test_rosetta_is_registered_as_a_soft_check_on_eval_and_evolve():
    severity, capabilities = CHECK_SPECS["rosetta"]
    assert severity == "soft"
    assert capabilities == ("eval", "evolve")


def test_rosetta_never_changes_the_exit_code():
    """I9, post-ruling: the check ships tagged to eval/evolve (not an empty
    tuple -- see the CHECK_SPECS comment), so safety no longer comes from
    "this check is tagged to nothing". It comes from two things holding at
    once: rosetta only ever emits "ok"/"warn" (never "fail" -- pinned
    separately by test_check_rosetta_never_emits_fail_for_any_rosetta_state
    below), and eval/evolve both carry hard checks of their own
    (container_runtime/arena_image here), so capability_ready's fold is
    gated PURELY by those and ignores every soft check tagged onto them --
    "ok" or "warn" alike. Assert both the concrete value (still 0/ready) and
    that no rosetta status can move it, for both capabilities rosetta is now
    tagged with."""
    from nethackers.diagnostics import CheckResult

    def results(rosetta_status: str) -> list[CheckResult]:
        return [
            CheckResult(id="container_runtime", status="ok", severity="hard",
                        detail="", fix=None, capabilities=("eval", "evolve")),
            CheckResult(id="arena_image", status="ok", severity="hard",
                        detail="", fix=None, capabilities=("eval", "evolve")),
            CheckResult(id="rosetta", status=rosetta_status, severity="soft",
                        detail="", fix=None, capabilities=("eval", "evolve")),
        ]

    for cap in ("eval", "evolve"):
        assert exit_code(results("ok"), cap) == 0
        assert exit_code(results("ok"), cap) == exit_code(results("warn"), cap)
        assert exit_code(results("ok"), cap) == exit_code(results("unknown"), cap)


# Every branch rosetta_state can take, paired with the (system, machine,
# settings-file content) input that drives it there -- used below to pin
# I9's other half: no matter which branch fires, `_check_rosetta` must never
# turn it into status="fail". `_ABSENT` is a distinct sentinel from `None`:
# `None` is itself a case under test below (a settings file containing the
# JSON literal `null`), so it can no longer double as "don't write a file".
_ABSENT = object()

_ROSETTA_STATE_CASES = [
    ("rosetta_on", "Darwin", "arm64",
     dict(UseVirtualizationFramework=True, UseVirtualizationFrameworkRosetta=True)),
    ("rosetta_off", "Darwin", "arm64",
     dict(UseVirtualizationFramework=False, UseVirtualizationFrameworkRosetta=False)),
    ("virtualization_framework_off", "Darwin", "arm64",
     dict(UseVirtualizationFramework=False, UseVirtualizationFrameworkRosetta=True)),
    ("unreadable_settings", "Darwin", "arm64", _ABSENT),
    ("intel_mac", "Darwin", "x86_64", _ABSENT),
    ("linux", "Linux", "x86_64", _ABSENT),
    # Present, valid JSON, but not the object shape Docker Desktop writes --
    # the "unguarded escape" a reviewer found by direct reproduction: `.get()`
    # on a list/None raises AttributeError, which `_safe`'s generic crash net
    # used to turn into status="fail".
    ("settings_file_is_a_json_list", "Darwin", "arm64", [1, 2, 3]),
    ("settings_file_is_json_null", "Darwin", "arm64", None),
]


@pytest.mark.parametrize(
    "system,machine,content",
    [case[1:] for case in _ROSETTA_STATE_CASES],
    ids=[case[0] for case in _ROSETTA_STATE_CASES],
)
def test_check_rosetta_never_emits_fail_for_any_rosetta_state(
    tmp_path, system, machine, content,
):
    """I9's other half, pinned directly against `_check_rosetta` rather than
    inferred: `CheckResult.status` admits "fail", but no `rosetta_state`
    outcome may ever produce one -- an advisory that can read as a hard
    failure would defeat the whole point. Covers every branch: Rosetta on,
    Rosetta off, virtualization framework off (still a warn -- Rosetta needs
    it), unreadable settings, an Intel Mac, Linux, and a settings file that
    is valid JSON but not an object (a list, or the literal `null`) -- the
    escape a narrower ``except (OSError, ValueError)`` alone would miss."""
    if content is _ABSENT:
        path = tmp_path / "absent.json"
    else:
        path = tmp_path / SETTINGS
        path.write_text(json.dumps(content))

    state = rosetta_state(system, machine, path)
    result = _check_rosetta(severity="soft", caps=("eval", "evolve"), rosetta=lambda: state)

    assert result.status in {"ok", "warn"}


def test_rosetta_warn_row_renders_in_render_human_output():
    """The regression this whole ruling exists to fix: with the original
    capabilities=() tagging, render_human/render_plain group rows by `cap in
    r.capabilities`, so the row could never appear in bare `nethackers
    doctor` output -- only in `-o json`. Now that rosetta is tagged
    eval/evolve, confirm the row (and its fix text) actually renders, and
    that the capability verdict still reads ready even though the row is a
    warn (INV6/I9 -- a soft warn never flips it)."""
    from nethackers.diagnostics import CheckResult, render_human

    results = [
        CheckResult(id="container_runtime", status="ok", severity="hard",
                    detail="docker is available", fix=None,
                    capabilities=("eval", "evolve")),
        CheckResult(id="rosetta", status="warn", severity="soft",
                    detail="amd64 evaluation is running under QEMU, not Rosetta",
                    fix="enable Rosetta in Docker Desktop settings",
                    capabilities=("eval", "evolve")),
    ]

    out = render_human(results)

    assert "rosetta" in out
    assert "enable Rosetta in Docker Desktop settings" in out
    assert "ready to eval" in out and "ready to evolve" in out
