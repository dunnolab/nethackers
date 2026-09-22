"""What setup prints. Pure functions: data in, rich markup out."""
from __future__ import annotations

from nethackers.setup import macos
from nethackers.setup.plan import GH_LOGIN, Plan, Step, Todo
from nethackers.setup.render import (
    Row,
    Summary,
    checklist,
    plan_lines,
    result_line,
    summary_markup,
    summary_plain,
)
from nethackers.setup.runner import StepResult
from nethackers.setup.support import Untested

INSTALL = Step("gh-install", "install the GitHub CLI", "captured", "brew install gh",
               ("brew", "install", "gh"), macos.GH_INSTALL)
LOGIN = Step("gh-login", "log you in to gh", "terminal", "gh auth login …", GH_LOGIN,
             needs=("gh-install",))
PULL = Step("pull", "pull the sandbox images", "pull", "875 MB, first time only",
            images=("arena", "mutator"))


def test_checklist_rows_carry_a_glyph_label_and_detail():
    lines = checklist([Row("ok", "hub login", "@you"), Row("fail", "gh", "not installed"),
                       Row("warn", "Rosetta", "off")])
    assert len(lines) == 3
    assert "[green]✓[/]" in lines[0] and "hub login" in lines[0] and "@you" in lines[0]
    assert "[red]✗[/]" in lines[1] and "[yellow]⚠[/]" in lines[2]


def test_plan_lines_number_the_steps_and_mark_untested_recipes():
    text = "\n".join(plan_lines(Plan(steps=(INSTALL, LOGIN, PULL)), machine="Mac"))
    assert "nethackers will:" in text
    assert "1  install the GitHub CLI" in text and "(untested)" in text
    assert "3  pull the sandbox images" in text and "875 MB, first time only" in text
    assert "Step 2 needs you at the keyboard; the rest run on their own." in text
    assert "haven't been run on a real Mac yet" in text


def test_plan_lines_align_columns_even_with_a_long_title():
    # plan.py's real same-account step title (36 chars) is longer than every
    # OS-recipe title; a fixed 34-char pad ran it straight into the command
    # column with no gap at all, while every other row still had one.
    long_title = Step("same-account", "check gh and the hub are one account", "same_account",
                      "compares the two GitHub logins")
    text = plan_lines(Plan(steps=(LOGIN, long_title)), machine="Mac")
    shows_at = [line.index("[cyan]") for line in text if "[cyan]" in line]
    assert len(shows_at) == 2 and shows_at[0] == shows_at[1]


def test_plan_lines_list_what_only_you_can_do_and_what_comes_after():
    plan = Plan(yours=(Todo("install Docker", "install Docker: `curl … | sudo sh`",
                            Untested("https://e.org")),),
                afterwards=(Todo("turn on Rosetta", "turn on Rosetta: …"),),
                notes=("No coding agent set up yet.",))
    text = "\n".join(plan_lines(plan, machine="Ubuntu 24.04 LTS"))
    assert "You'll need to (nethackers never runs sudo):" in text
    assert "install Docker: `curl … | sudo sh`" in text
    assert "Afterwards, only you can do this:" in text
    assert "No coding agent set up yet." in text
    assert "real Ubuntu 24.04 LTS yet" in text


def test_result_lines():
    assert "34 s" in result_line(INSTALL, StepResult(True, 34.2))
    assert "875 MB in 51 s" in result_line(PULL, StepResult(True, 51.0, "875 MB"))
    failed = result_line(LOGIN, StepResult(False, 3.0, "exited 1"))
    assert "[red]✗[/]" in failed and "exited 1" in failed
    skipped = result_line(LOGIN, StepResult(False, 0.0, "needs: install the GitHub CLI",
                                            skipped=True))
    assert "skipped" in skipped and "install the GitHub CLI" in skipped


def test_summary_when_everything_is_ready():
    s = Summary(ready=("eval", "evolve", "publish", "browse"), not_ready=(), failing=(),
                yours=(), afterwards=(), next_command=None, nothing_to_do=True)
    assert summary_markup(s).startswith("[green]✓[/] ready to eval · evolve · publish · browse.")
    assert "Nothing to do." in summary_markup(s)
    assert summary_plain(s).startswith("OK ready to eval · evolve · publish · browse.")


def test_summary_when_something_is_left():
    s = Summary(ready=("eval",), not_ready=("publish",), failing=(("gh", "gh is not installed"),),
                yours=(Todo("install gh", "install the GitHub CLI: `sudo apt install gh`"),),
                afterwards=(),
                next_command="nethackers eval ./my-bot --objective val-dwa-law-fem")
    text = summary_markup(s)
    assert "not ready to publish" in text and "gh: gh is not installed" in text
    assert "sudo apt install gh" in text
    assert "run `nethackers setup` again" in text
    assert "Next: nethackers eval ./my-bot" in text
    assert "[" not in summary_plain(s)          # no rich markup in the plain variant


def test_summary_shortens_a_pinned_image_digest_like_doctor_does():
    # arena_image/mutator_image's real detail carries the full pinned ref
    # (~150 chars with a digest); doctor's own render shortens it
    # (diagnostics._short_digest) so it doesn't hard-wrap here either.
    ref = "ghcr.io/dunnolab/nethackers-mutator@sha256:" + "a" * 64
    s = Summary(ready=(), not_ready=("evolve",),
               failing=(("mutator image", f"not local yet, but pullable — {ref}"),),
               yours=(), afterwards=(), next_command=None)
    assert ref not in summary_markup(s) and ref not in summary_plain(s)
    assert f"sha256:{'a' * 19}…" in summary_markup(s)
