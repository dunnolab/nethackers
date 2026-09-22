"""What setup prints. Pure functions: data in, rich renderables out -- so every
assertion about the look renders through a recording Console first."""
from __future__ import annotations

import io
import re
from dataclasses import replace

import pytest
from rich.console import Console, RenderableType
from rich.table import Table

from nethackers.containers import RuntimeCandidate, RuntimeReport
from nethackers.diagnostics import CHECK_SPECS, CheckResult
from nethackers.setup import linux, macos
from nethackers.setup.host import HostFacts
from nethackers.setup.plan import GH_LOGIN, Plan, Situation, Step, Todo, build_plan
from nethackers.setup.render import (
    Row,
    Summary,
    checklist,
    plan_lines,
    result_line,
    summary_plain,
    summary_rich,
)
from nethackers.setup.runner import StepResult
from nethackers.setup.support import Untested

INSTALL = Step("gh-install", "install the GitHub CLI", "captured", "brew install gh",
               ("brew", "install", "gh"), macos.GH_INSTALL)
LOGIN = Step("gh-login", "log you in to gh", "terminal", "gh auth login …", GH_LOGIN,
             needs=("gh-install",))
PULL = Step("pull", "pull the sandbox images", "pull", "up to 875 MB",
            images=("arena", "mutator"))


def text_of(*renderables: RenderableType, width: int = 100) -> str:
    """What a terminal ``width`` columns wide shows, trailing spaces dropped."""
    console = Console(file=io.StringIO(), width=width, record=True, color_system=None)
    for renderable in renderables:
        console.print(renderable)
    return "\n".join(line.rstrip() for line in console.export_text().splitlines())


def _fresh_mac_plan() -> Plan:
    """The longest plan setup makes: a fresh 16 GB Mac with Homebrew."""
    down = {"container_runtime": "fail", "arena_image": "fail", "mutator_image": "fail",
            "hub_login": "fail", "gh": "fail"}
    checks = tuple(CheckResult(id=cid, status=down.get(cid, "ok"), severity=sev, detail="",
                               fix=None, capabilities=caps)
                   for cid, (sev, caps) in CHECK_SPECS.items())
    none = RuntimeReport(None, (RuntimeCandidate("docker", "absent", ""),
                                RuntimeCandidate("podman", "absent", "")))
    facts = HostFacts("Darwin", "arm64", brew=True, host_rosetta=True, cpus=8, memory_gb=16)
    return build_plan(Situation(checks=checks, facts=facts, runtime=none, scope=None,
                                agent="claude", agent_installed=False, agent_logged_in=False,
                                hub_login=None, gh_login=None, gh_state="missing",
                                pull_size=875_000_000), macos)


def test_checklist_rows_carry_a_glyph_label_and_detail():
    lines = text_of(checklist([Row("ok", "hub login", "@you"), Row("fail", "gh", "not installed"),
                               Row("warn", "Rosetta", "off")])).splitlines()
    assert lines == ["  ✓ hub login           @you", "  ✗ gh                  not installed",
                     "  ⚠ Rosetta             off"]


def test_a_long_checklist_detail_wraps_under_its_column():
    lines = text_of(checklist([Row("fail", "container runtime", "docker: exited 1: " + "x " * 40)]),
                    width=80).splitlines()
    assert len(lines) > 1 and all(line.startswith(" " * 24) for line in lines[1:])


def test_plan_lines_number_the_steps_and_mark_untested_recipes():
    text = text_of(*plan_lines(Plan(steps=(INSTALL, LOGIN, PULL)), machine="Mac"))
    assert "nethackers will:" in text
    assert "  1 install the GitHub CLI" in text and "(untested)" in text
    assert "  3 pull the sandbox images" in text and "up to 875 MB" in text
    assert "Step 2 needs you at the keyboard; the rest run on their own." in text
    assert "haven't been run on a real Mac yet" in text


def test_plan_lines_align_columns_even_with_a_long_title():
    # plan.py's real same-account step title (36 chars) is longer than every
    # OS-recipe title; a fixed 34-char pad once ran it straight into the
    # command column with no gap at all, while every other row still had one.
    long_title = Step("same-account", "check gh and the hub are one account", "same_account",
                      "compares the two GitHub logins")
    lines = text_of(*plan_lines(Plan(steps=(LOGIN, long_title)), machine="Mac")).splitlines()
    starts = [line.index("gh auth login …") for line in lines if "gh auth login" in line]
    starts += [line.index("compares") for line in lines if "compares" in line]
    assert len(starts) == 2 and starts[0] == starts[1]
    assert "one account compares" in text_of(*plan_lines(Plan(steps=(long_title,)),
                                                        machine="Mac"))


def _step_cells(grid_text: str, steps: tuple[Step, ...]) -> tuple[int, list[list[str]]]:
    """The command column's offset (number, gap, widest title, gap), and each
    step's lines: a step starts where its number sits in the first 3 columns."""
    column = 3 + 1 + max(len(step.title) for step in steps) + 1
    cells: list[list[str]] = []
    for line in grid_text.splitlines():
        if len(cells) < len(steps) and re.match(rf"^ {{0,2}}{len(cells) + 1} \S", line):
            cells.append([line])
        else:
            cells[-1].append(line)
    return column, cells


@pytest.mark.parametrize("width", [80, 100])
def test_a_long_plan_wraps_inside_its_columns_not_back_to_column_0(width):
    plan = replace(_fresh_mac_plan(),
                   yours=(Todo("install Docker", linux.DOCKER_INSTALL.say,
                               linux.DOCKER_INSTALL.support),
                          Todo("fix Podman", linux.PODMAN_FIX.say, linux.PODMAN_FIX.support)),
                   afterwards=(Todo("Rosetta", macos.ROSETTA_DOCKER_DESKTOP.say,
                                    macos.ROSETTA_DOCKER_DESKTOP.support),))
    steps_grid, yours_grid, after_grid = (p for p in plan_lines(plan, machine="Mac")
                                          if isinstance(p, Table))
    column, cells = _step_cells(text_of(steps_grid, width=width), plan.steps)
    assert len(cells) == len(plan.steps) == 9
    assert any(len(cell) > 1 for cell in cells)                    # something did wrap
    for step, cell in zip(plan.steps, cells, strict=True):
        pieces = [cell[0][column:]]
        for line in cell[1:]:
            assert line[:column].strip() == "" and line[column] != " ", line   # in the column
            pieces.append(line[column:])
        assert "".join(pieces).replace(" ", "").startswith(step.shows.replace(" ", ""))
    for grid, todos in ((yours_grid, plan.yours), (after_grid, plan.afterwards)):
        lines = text_of(grid, width=width).splitlines()
        assert sum(line.startswith("  • ") for line in lines) == len(todos)
        for line in lines:
            assert line.startswith("  • ") or re.match(r"^    \S", line), line
        joined = "".join(line[4:] for line in lines).replace(" ", "")
        assert all(t.say.replace(" ", "") in joined for t in todos)


def test_plan_lines_list_what_only_you_can_do_and_what_comes_after():
    plan = Plan(yours=(Todo("install Docker", "install Docker: `curl … | sudo sh`",
                            Untested("https://e.org")),),
                afterwards=(Todo("turn on Rosetta", "turn on Rosetta: …"),),
                notes=("No coding agent set up yet.",))
    text = text_of(*plan_lines(plan, machine="Ubuntu 24.04 LTS"))
    assert "You'll need to (nethackers never runs sudo):" in text
    assert "  • install Docker: `curl … | sudo sh`  (untested)" in text
    assert "Afterwards, only you can do this:" in text
    assert "No coding agent set up yet." in text
    assert "real Ubuntu 24.04 LTS yet" in text


def test_result_lines():
    assert "34 s" in result_line(INSTALL, StepResult(True, 34.2))
    pulled = result_line(PULL, StepResult(True, 51.0))
    assert "pull the sandbox images" in pulled and "51 s" in pulled and "MB" not in pulled
    failed = result_line(LOGIN, StepResult(False, 3.0, "exited 1"))
    assert "[red]✗[/]" in failed and "exited 1" in failed
    skipped = result_line(LOGIN, StepResult(False, 0.0, "needs: install the GitHub CLI",
                                            skipped=True))
    assert "skipped" in skipped and "install the GitHub CLI" in skipped


def test_summary_when_everything_is_ready():
    s = Summary(ready=("eval", "evolve", "publish", "browse"), not_ready=(), failing=(),
                yours=(), afterwards=(), next_command=None, nothing_to_do=True)
    assert text_of(summary_rich(s)) == "✓ ready to eval · evolve · publish · browse. Nothing to do."
    assert summary_plain(s).startswith("OK ready to eval · evolve · publish · browse.")


def test_summary_when_something_is_left():
    s = Summary(ready=("eval",), not_ready=("publish",), failing=(("gh", "gh is not installed"),),
                yours=(Todo("install gh", "install the GitHub CLI: `sudo apt install gh`"),),
                afterwards=(),
                next_command="nethackers eval ./my-bot --objective val-dwa-law-fem")
    for text in (text_of(summary_rich(s)), summary_plain(s)):
        assert "not ready to publish" in text and "gh: gh is not installed" in text
        assert "  • install the GitHub CLI: `sudo apt install gh`" in text
        assert "run `nethackers setup` again" in text
        assert "Next: nethackers eval ./my-bot" in text
    assert "[" not in summary_plain(s)          # no rich markup in the plain variant


def test_summary_rows_and_bullets_wrap_inside_their_columns():
    s = Summary(ready=(), not_ready=("eval",),
                failing=(("container runtime", "docker: exited 1: " + "x " * 30),
                         ("hub login", "not logged in")),
                yours=(Todo("install Docker", linux.DOCKER_INSTALL.say),), afterwards=(),
                next_command=None)
    lines = text_of(summary_rich(s), width=80).splitlines()
    rows = lines[lines.index("✗ not ready to eval") + 1:lines.index(
        "Still to do (nethackers never runs sudo):")]
    assert rows[0].startswith("  container runtime: docker: exited 1:")
    assert all(line.startswith(" " * 21) for line in rows[1:-1])
    assert rows[-1] == "  hub login:         not logged in"
    bullets = lines[lines.index("Still to do (nethackers never runs sudo):") + 1:-1]
    assert bullets[0].startswith("  • install Docker:") and len(bullets) > 1
    assert all(line.startswith("    ") for line in bullets[1:])


def test_the_summary_is_not_recoloured_by_richs_highlighter():
    # Printed by `emit` through a Console with highlighting on: numbers and
    # digest fragments must keep the summary's own colours only.
    s = Summary(ready=(), not_ready=("eval",), failing=(("container runtime", "exited 1"),),
                yours=(), afterwards=(), next_command=None)
    buf = io.StringIO()
    Console(file=buf, force_terminal=True, color_system="standard", width=100).print(
        summary_rich(s))
    assert "container runtime:" in buf.getvalue() and "exited 1" in buf.getvalue()


def test_summary_shortens_a_pinned_image_digest_like_doctor_does():
    # arena_image/mutator_image's real detail carries the full pinned ref
    # (~150 chars with a digest); doctor's own render shortens it
    # (diagnostics._short_digest) so it doesn't hard-wrap here either.
    ref = "ghcr.io/dunnolab/nethackers-mutator@sha256:" + "a" * 64
    s = Summary(ready=(), not_ready=("evolve",),
                failing=(("mutator image", f"not local yet, but pullable — {ref}"),),
                yours=(), afterwards=(), next_command=None)
    rich_text = text_of(summary_rich(s), width=200)
    assert ref not in rich_text and ref not in summary_plain(s)
    assert f"sha256:{'a' * 19}…" in rich_text and f"sha256:{'a' * 19}…" in summary_plain(s)
