"""doctor's Rosetta row: an advisory that never gates. The per-runtime logic
lives in setup/macos.py (tests/test_setup_macos.py); this file pins how
doctor folds and renders it."""
from pathlib import Path

import pytest

from nethackers.diagnostics import CHECK_SPECS, CheckResult, _check_rosetta, exit_code
from nethackers.setup import linux, macos
from nethackers.setup.host import HostFacts

HOME = Path("/Users/you")
DESKTOP: dict = dict(installed=frozenset({"docker", "docker-desktop"}),
                     docker_context="desktop-linux")


def test_rosetta_is_registered_as_a_soft_check_on_eval_and_evolve():
    severity, capabilities = CHECK_SPECS["rosetta"]
    assert severity == "soft"
    assert capabilities == ("eval", "evolve")


def test_rosetta_never_changes_the_exit_code():
    """I9: rosetta only ever emits "ok"/"warn", and eval/evolve are gated
    purely by their own hard checks -- so no rosetta status can move the exit
    code."""
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


_CASES = [
    ("desktop_on", HostFacts("Darwin", "arm64", home=HOME, host_rosetta=True, **DESKTOP),
     '{"UseVirtualizationFramework": true, "UseVirtualizationFrameworkRosetta": true}'),
    ("desktop_off", HostFacts("Darwin", "arm64", home=HOME, host_rosetta=True, **DESKTOP),
     '{"UseVirtualizationFramework": true, "UseVirtualizationFrameworkRosetta": false}'),
    ("desktop_keys_missing", HostFacts("Darwin", "arm64", home=HOME, host_rosetta=True,
                                       **DESKTOP), "{}"),
    ("desktop_unreadable", HostFacts("Darwin", "arm64", home=HOME, host_rosetta=True,
                                     **DESKTOP), None),
    ("desktop_json_list", HostFacts("Darwin", "arm64", home=HOME, host_rosetta=True,
                                    **DESKTOP), "[1, 2, 3]"),
    ("no_host_rosetta", HostFacts("Darwin", "arm64", home=HOME, host_rosetta=False), None),
    ("podman", HostFacts("Darwin", "arm64", home=HOME, host_rosetta=True,
                         installed=frozenset({"podman"})), None),
    ("intel_mac", HostFacts("Darwin", "x86_64", home=HOME), None),
]


@pytest.mark.parametrize("facts,settings", [c[1:] for c in _CASES], ids=[c[0] for c in _CASES])
def test_check_rosetta_never_emits_fail(facts, settings):
    state, detail, recipe = macos.emulation(facts, read_text=lambda path: settings)
    result = _check_rosetta(severity="soft", caps=("eval", "evolve"),
                            rosetta=lambda: (state, detail, recipe.say if recipe else None))
    assert result.status in {"ok", "warn"}
    assert (result.fix is None) == (result.status == "ok")


def test_linux_never_emits_fail():
    state, detail, _ = linux.emulation(HostFacts("Linux", "x86_64"))
    assert _check_rosetta(severity="soft", caps=("eval", "evolve"),
                          rosetta=lambda: (state, detail, None)).status == "ok"


def test_rosetta_warn_row_renders_in_render_human_output():
    from nethackers.diagnostics import render_human

    results = [
        CheckResult(id="container_runtime", status="ok", severity="hard",
                    detail="docker is available", fix=None, capabilities=("eval", "evolve")),
        CheckResult(id="rosetta", status="warn", severity="soft",
                    detail="amd64 evaluation is running under QEMU, not Rosetta",
                    fix=macos.ROSETTA_DOCKER_DESKTOP.say, capabilities=("eval", "evolve")),
    ]
    out = render_human(results)
    assert "rosetta" in out and "Use Rosetta for x86_64/amd64 emulation" in out
    assert "ready to eval" in out and "ready to evolve" in out
