"""The recipe record every OS file is made of, and the helpers that turn
recipes into doctor's fix line, the plan's command column, and the docs table."""
from __future__ import annotations

import pytest

from nethackers.setup.support import (
    NotCovered,
    Recipe,
    Tested,
    Untested,
    fix_text,
    mb_text,
    render_table,
    shown_command,
    support_label,
)

RUN = Recipe(id="macos.x.install", does="install X", who="nethackers",
             argv=("brew", "install", "x"), support=Untested("https://example.org/x"))
SAY = Recipe(id="linux.x.install", does="install X", who="you",
             say="install X: `sudo apt install x`", support=Untested("https://example.org/x"))


def test_a_recipe_nethackers_runs_needs_argv():
    with pytest.raises(ValueError, match="needs argv"):
        Recipe(id="a", does="b", who="nethackers", support=Untested("https://e.org"))


def test_a_printed_recipe_needs_something_to_say():
    with pytest.raises(ValueError, match="needs say"):
        Recipe(id="a", does="b", who="you", support=Untested("https://e.org"))


def test_shown_command_prints_a_shell_pipeline_as_its_script():
    argv = ("sh", "-c", "curl -fsSL https://claude.ai/install.sh | bash")
    assert shown_command(argv) == "curl -fsSL https://claude.ai/install.sh | bash"


def test_shown_command_quotes_everything_else():
    assert shown_command(("brew", "install", "colima", "docker")) == "brew install colima docker"
    assert shown_command(("echo", "a b")) == "echo 'a b'"


def test_support_labels_use_exactly_three_words():
    assert support_label(Tested("macOS 15.5 arm64", "0.35.0", "2026-09-25")) == "tested"
    assert support_label(Untested("https://e.org")) == "untested"
    assert support_label(NotCovered("https://e.org")) == "not covered"


def test_fix_text_points_at_setup_when_setup_can_do_any_of_it():
    assert fix_text([SAY, RUN]) == "run `nethackers setup`"


def test_fix_text_prints_what_only_you_can_do_then_setup():
    assert fix_text([SAY]) == "install X: `sudo apt install x`; then run `nethackers setup`"
    assert fix_text([SAY], then_setup=False) == "install X: `sudo apt install x`"


def test_fix_text_is_none_when_there_is_nothing_to_do():
    assert fix_text([]) is None


def test_mb_text_uses_decimal_megabytes_like_docker():
    assert mb_text(875_000_000) == "875 MB"
    assert mb_text(432_400_000) == "432 MB"


def test_render_table_has_one_row_per_recipe_with_its_status():
    tested = Recipe(id="macos.y.start", does="start Y", who="nethackers", argv=("y", "start"),
                    support=Tested("macOS 15.5 arm64", "0.35.0", "2026-09-25"))
    piped = Recipe(id="macos.z.install", does="install Z", who="nethackers",
                   argv=("sh", "-c", "curl -fsSL https://z.example | sh"),
                   support=Untested("https://z.example/doc"))
    gone = Recipe(id="linux.w.install", does="install W", who="you",
                  say="install W from https://w.example", support=NotCovered("https://w.example"))
    text = render_table([("macOS", [tested, piped]), ("Linux", [gone])])
    assert text.startswith("### macOS\n")
    assert ("| `macos.y.start` | start Y | nethackers | `y start` | "
            "tested on macOS 15.5 arm64 (nethackers 0.35.0, 2026-09-25) |") in text
    assert "`curl -fsSL https://z.example \\| sh`" in text          # pipes escaped for the table
    assert "untested ([built from](https://z.example/doc))" in text
    assert "### Linux" in text and "not covered ([vendor page](https://w.example))" in text
