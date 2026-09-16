"""The agent-facing markdown brief: what it says, and what it must never say.

Two data states matter and both are exercised: a populated store (the numbers
appear) and an empty store on which genesis has run (the reset sentence
appears, and no date is invented).
"""

import re
from pathlib import Path

import pytest

from nethackers._image_pins import ARENA_IMAGE
from nethackers.arena.seeds import secret_fingerprint
from nethackers.arena_version import ARENA_MAJOR
from nethackers.contracts.models import Atom
from nethackers.hub.store import Store
from nethackers.hub.views.brief import render_brief
from nethackers.hub.views.source import Epoch

REPO_ROOT = Path(__file__).resolve().parents[2]
IDENT = "val-dwa-law-fem"
SECRET = "dev-secret"
HIDDEN_SEEDS = (48151623, 42108642)


def _atom(seed: int, *, tier: str, progression: float = 0.4) -> Atom:
    return Atom(solution_digest="sha256:a", owner="sam", tier=tier,
                identity=IDENT, seed=seed, progression=progression,
                milestone="Dlvl:5", ascended=False, status="completed",
                turns=10, steps=20, evaluator_image=ARENA_IMAGE)


@pytest.fixture()
def empty_store(tmp_path):
    store = Store(str(tmp_path / "hub.db"))
    store.init_schema()
    return store


@pytest.fixture()
def populated_store(empty_store):
    empty_store.upsert_solution(
        "sha256:a", repo="github.com/sam/nethacker", commit_sha="a" * 40,
        owner="sam", root=".", entrypoint="bot.py",
        registered_at="2026-09-16T10:00:00Z")
    empty_store.insert_atoms([_atom(1, tier="self-reported")])
    empty_store.insert_baseline_atoms([_atom(1, tier="baseline", progression=0.1)])
    return empty_store


def test_the_numbers_come_from_the_store(populated_store):
    brief = render_brief(populated_store, version="9.9.9")
    assert "| Programs registered | 1 |" in brief
    assert "| Hackers | 1 |" in brief
    assert "2026-09-16T10:00:00Z" in brief
    assert "9.9.9" in brief
    assert f"arena major {ARENA_MAJOR}" in brief


def test_no_placeholder_survives_rendering(populated_store):
    brief = render_brief(populated_store, version="9.9.9")
    assert "{{" not in brief, "an unfilled slot would ship to an agent verbatim"


def test_an_empty_board_is_not_dressed_up_as_data(empty_store):
    brief = render_brief(empty_store, version="9.9.9")
    assert "| Programs registered | 0 |" in brief
    assert "| Last registration | — |" in brief


def test_a_reset_board_says_so(empty_store):
    """Without this sentence, four separate agents read the zeros as data loss."""
    empty_store.genesis()
    brief = render_brief(empty_store, version="9.9.9")
    assert "archived" in brief
    assert "not comparable" in brief


def test_an_un_reset_board_makes_no_such_claim(empty_store):
    brief = render_brief(empty_store, version="9.9.9")
    assert "archived" not in brief


def test_no_date_is_invented_for_the_reset(empty_store):
    """genesis records no timestamp (D7). Any date here would be a fabrication."""
    empty_store.genesis()
    brief = render_brief(empty_store, version="9.9.9")
    reset_sentence = [ln for ln in brief.splitlines() if "archived" in ln]
    assert reset_sentence
    for line in reset_sentence:
        assert not re.search(r"\d{4}-\d{2}-\d{2}", line)


def test_a_hub_without_a_verifier_still_has_a_brief(populated_store):
    """`epoch=None` means verification is unconfigured. The front page must not
    503 for every markdown client -- the private floor just drops out."""
    brief = render_brief(populated_store, epoch=None, version="9.9.9")
    assert "Private Dungeons" in brief          # the explanation stays
    assert "on Private Dungeons." not in brief  # the floor figure does not


def test_the_brief_never_leaks_a_hidden_seed(populated_store):
    """I5. The private tier is worthless the moment its seeds are public."""
    populated_store.insert_verified_atoms(
        [_atom(seed, tier="verified") for seed in HIDDEN_SEEDS],
        secret_fingerprint=secret_fingerprint(SECRET),
        verifier_token_fingerprint="tok", arena_major=ARENA_MAJOR)
    populated_store.insert_verified_baseline_atoms(
        [_atom(seed, tier="baseline", progression=0.123) for seed in HIDDEN_SEEDS],
        secret_fingerprint=secret_fingerprint(SECRET),
        verifier_token_fingerprint="tok", arena_major=ARENA_MAJOR)
    epoch = Epoch(secret_fingerprint=secret_fingerprint(SECRET),
                  arena_major=ARENA_MAJOR, seeds=HIDDEN_SEEDS)
    brief = render_brief(populated_store, epoch=epoch, version="9.9.9")
    for seed in HIDDEN_SEEDS:
        assert str(seed) not in brief


def test_the_brief_fits_the_budget(populated_store):
    """I7. The point of the brief is that it is cheap to read."""
    brief = render_brief(populated_store, version="9.9.9")
    assert len(brief.encode("utf-8")) <= 4096


def _commands(text: str) -> list[str]:
    """Every shell line inside a ```bash fence, normalised: inline comments
    dropped, backslash continuations joined, whitespace collapsed."""
    joined = text.replace("\\\n", " ")
    out = []
    for block in re.findall(r"```bash\n(.*?)```", joined, re.S):
        for line in block.splitlines():
            line = line.split("#")[0].strip()
            if line:
                out.append(" ".join(line.split()))
    return out


def test_every_command_in_the_brief_is_in_the_readme(populated_store):
    """The README is the source of truth for install instructions; the brief is
    a condensation of it. Two copies drift, and only one of them is read by a
    human maintaining the project -- so drift is a red test, not a wrong
    command served to an agent."""
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    readme_norm = " ".join(readme.replace("\\\n", " ").split())
    brief = render_brief(populated_store, version="9.9.9")
    missing = [c for c in _commands(brief) if c not in readme_norm]
    assert not missing, f"not in README.md: {missing}"
