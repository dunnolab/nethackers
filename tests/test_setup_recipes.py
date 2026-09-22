"""Rules every OS recipe obeys, and the docs table that must match them."""
from __future__ import annotations

import re

from nethackers.setup import docs, linux, macos
from nethackers.setup.host import HostFacts, platform_for
from nethackers.setup.plan import AGENT_LOGIN, GH_LOGIN
from nethackers.setup.support import NotCovered, Tested, Untested

ALL = macos.RECIPES + linux.RECIPES
_SUDO = re.compile(r"\bsudo\b")


def test_recipe_ids_are_unique_and_name_their_os():
    ids = [r.id for r in ALL]
    assert len(ids) == len(set(ids))
    assert all(r.id.startswith("macos.") for r in macos.RECIPES)
    assert all(r.id.startswith("linux.") for r in linux.RECIPES)


def test_nothing_nethackers_runs_calls_sudo():
    sized = macos.colima_start(HostFacts("Darwin", "arm64", cpus=8, memory_gb=16))
    for recipe in (*ALL, sized):
        if recipe.who == "nethackers":
            assert not any(_SUDO.search(part) for part in recipe.argv), recipe.id
    for argv in (GH_LOGIN, *AGENT_LOGIN.values()):   # the logins setup runs itself
        assert not any(_SUDO.search(part) for part in argv), argv


def test_every_untested_recipe_links_the_document_it_follows():
    for recipe in ALL:
        if isinstance(recipe.support, Untested):
            assert recipe.support.built_from.startswith("https://"), recipe.id


def test_every_tested_recipe_says_where_at_which_version_and_when():
    for recipe in ALL:
        if isinstance(recipe.support, Tested):
            assert recipe.support.on, recipe.id
            assert re.fullmatch(r"\d+\.\d+\.\d+", recipe.support.at), recipe.id
            assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", recipe.support.date), recipe.id


def test_not_covered_recipes_are_printed_and_link_the_vendor_page():
    for recipe in ALL:
        if isinstance(recipe.support, NotCovered):
            assert recipe.who == "you", recipe.id
            assert recipe.support.link.startswith("https://"), recipe.id


def test_the_docs_table_matches_the_recipes():
    text = docs.DOC.read_text(encoding="utf-8")
    assert text == docs.with_table(text), (
        "docs/setup.md is stale: run `uv run python -m nethackers.setup.docs`")


def test_platform_for_picks_the_os_file():
    assert platform_for(HostFacts("Darwin", "arm64")) is macos
    assert platform_for(HostFacts("Linux", "x86_64")) is linux
    assert platform_for(HostFacts("Windows", "AMD64")) is None


def test_both_os_files_offer_the_same_interface():
    for name in ("RECIPES", "runtime_recipes", "gh_install_recipe", "agent_install_recipe",
                 "emulation"):
        assert hasattr(macos, name) and hasattr(linux, name), name
