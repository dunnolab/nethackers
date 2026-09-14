import json
from pathlib import Path

from nethackers.diagnostics import CHECK_SPECS, exit_code, rosetta_state

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


def test_rosetta_is_registered_as_a_soft_check_gating_nothing():
    severity, capabilities = CHECK_SPECS["rosetta"]
    assert severity == "soft"
    assert capabilities == ()


def test_rosetta_never_changes_the_exit_code():
    """INV6/I9: a warn gates nothing, and an empty capability tuple gates
    nothing either. Assert both directions explicitly."""
    from nethackers.diagnostics import CheckResult

    def results(rosetta_status: str) -> list[CheckResult]:
        return [
            CheckResult(id="container_runtime", status="ok", severity="hard",
                        detail="", fix=None, capabilities=("eval", "evolve")),
            CheckResult(id="arena_image", status="ok", severity="hard",
                        detail="", fix=None, capabilities=("eval", "evolve")),
            CheckResult(id="rosetta", status=rosetta_status, severity="soft",
                        detail="", fix=None, capabilities=()),
        ]

    assert exit_code(results("ok"), "eval") == exit_code(results("warn"), "eval")
    assert exit_code(results("ok"), "eval") == exit_code(results("unknown"), "eval")
