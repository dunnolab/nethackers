"""The agent-facing markdown brief: what it says, and what it must never say.

Store shape drives most of what changes. `empty_store`/`populated_store`
cover the two baseline cases; `rebuilding_store` is the one production
actually had on 2026-09-16 (an archived epoch with rows already
re-registered on it, programs=5) -- the shape that makes the unconditional
"zero registered programs" sentence a lie. `worst_case_store` stacks every
optional section at once (archive, rebuilding, a verified epoch) for the
byte budget (I7), which only the realistic worst case can actually enforce.
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


def _atom(seed: int, *, tier: str, progression: float = 0.4,
          ascended: bool = False) -> Atom:
    return Atom(solution_digest="sha256:a", owner="sam", tier=tier,
                identity=IDENT, seed=seed, progression=progression,
                milestone="Dlvl:5", ascended=ascended, status="completed",
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


@pytest.fixture()
def rebuilding_store(empty_store):
    """Archive present AND rows already on the live board -- production's
    actual shape on 2026-09-16 (programs=5). Order is load-bearing: genesis
    must run BEFORE the insert, or it would archive these very rows and the
    fixture would silently collapse into the zero-programs state instead."""
    empty_store.genesis()
    empty_store.upsert_solution(
        "sha256:a", repo="github.com/sam/nethacker", commit_sha="a" * 40,
        owner="sam", root=".", entrypoint="bot.py",
        registered_at="2026-09-16T10:00:00Z")
    empty_store.insert_atoms([_atom(1, tier="self-reported")])
    empty_store.insert_baseline_atoms([_atom(1, tier="baseline", progression=0.1)])
    return empty_store


@pytest.fixture()
def worst_case_store(rebuilding_store):
    """Every optional line the document can carry, present at once: the
    longer "rebuilding" bullet (not the shorter empty-board one) and a
    verified epoch, so the Private Dungeons floor line renders too."""
    rebuilding_store.insert_verified_atoms(
        [_atom(seed, tier="verified") for seed in HIDDEN_SEEDS],
        secret_fingerprint=secret_fingerprint(SECRET),
        verifier_token_fingerprint="tok", arena_major=ARENA_MAJOR)
    rebuilding_store.insert_verified_baseline_atoms(
        [_atom(seed, tier="baseline", progression=0.123) for seed in HIDDEN_SEEDS],
        secret_fingerprint=secret_fingerprint(SECRET),
        verifier_token_fingerprint="tok", arena_major=ARENA_MAJOR)
    return rebuilding_store


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


def test_an_unmeasured_best_is_still_a_dash(empty_store):
    """FIX5 keeps this behaviour: 0.0 means "no atoms", not "scored zero",
    so it must not start rendering as a rounded 0.0 once rounding is added."""
    brief = render_brief(empty_store, version="9.9.9")
    assert "| Best progression | — |" in brief


def test_the_best_progression_is_rounded_to_three_places(empty_store):
    """Production today (2026-09-16): stats['best'] is a raw float and would
    render as `0.33264942385807844` beside floor figures already rounded to
    3 dp by per_identity_fold."""
    empty_store.upsert_solution(
        "sha256:a", repo="github.com/sam/nethacker", commit_sha="a" * 40,
        owner="sam", root=".", entrypoint="bot.py",
        registered_at="2026-09-16T10:00:00Z")
    empty_store.insert_atoms(
        [_atom(1, tier="self-reported", progression=0.33264942385807844)])
    brief = render_brief(empty_store, version="9.9.9")
    assert "| Best progression | 0.333 |" in brief
    assert "0.33264942385807844" not in brief


def test_a_reset_board_says_so(empty_store):
    """FIX1 state 1 (archive, zero programs): without this sentence, four
    separate agents read the zeros as data loss."""
    empty_store.genesis()
    brief = render_brief(empty_store, version="9.9.9")
    assert "Zero registered programs is the expected state" in brief
    assert "archived" in brief
    assert "not comparable" in brief
    assert "Contributors re-register." in brief


def test_a_rebuilding_board_says_so_instead(rebuilding_store):
    """FIX1 state 2 (archive, programs > 0): the state1 sentence above would
    sit false the moment the board has rows on it -- production did, on
    2026-09-16, twelve lines above `| Programs registered | 5 |`."""
    brief = render_brief(rebuilding_store, version="9.9.9")
    assert "| Programs registered | 1 |" in brief
    assert "The board is rebuilding, so the numbers are small." in brief
    assert "Zero registered programs is the expected state" not in brief
    assert "archived" in brief                    # the explanation still runs
    assert "not comparable" in brief
    assert "Contributors are re-registering." in brief  # continuous tense,
                                                          # not the empty-
                                                          # board wording


def test_an_un_reset_board_makes_no_such_claim(empty_store):
    """FIX1 state 3 (no archive): a hub that was never reset has nothing to
    explain, so the bullet -- and the blank line it would otherwise leave
    behind -- is gone, not merely empty."""
    brief = render_brief(empty_store, version="9.9.9")
    assert "archived" not in brief
    assert "Zero registered programs" not in brief
    assert "rebuilding" not in brief
    section = brief.split("## Read this first\n", 1)[1]
    assert section.lstrip("\n").startswith(
        "- **Scores are measured on linux/amd64.**"
    ), "omitting the bullet must not leave a stray blank line before the next one"


def test_no_date_is_invented_for_the_reset(empty_store):
    """genesis records no timestamp (D7). Any date here would be a fabrication."""
    empty_store.genesis()
    brief = render_brief(empty_store, version="9.9.9")
    reset_sentence = [ln for ln in brief.splitlines() if "archived" in ln]
    assert reset_sentence
    for line in reset_sentence:
        assert not re.search(r"\d{4}-\d{2}-\d{2}", line)


def test_the_ascension_clause_holds_while_the_count_is_zero(populated_store):
    brief = render_brief(populated_store, version="9.9.9")
    assert "| Ascensions | 0 |" in brief
    assert "The north star is an ascension; nothing has managed one yet." in brief


def test_the_ascension_clause_drops_once_one_lands(populated_store):
    """FIX2 / I3: the brief transcribes no figures, so this clause must track
    stats["ascensions"] -- fixed prose here would have the first ascension
    contradict the `| Ascensions | 1 |` row three paragraphs up."""
    populated_store.insert_atoms([_atom(2, tier="self-reported", ascended=True)])
    brief = render_brief(populated_store, version="9.9.9")
    assert "| Ascensions | 1 |" in brief
    assert "nothing has managed one yet" not in brief
    assert "The north star is an ascension." in brief


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


def test_the_brief_fits_the_budget(worst_case_store):
    """I7. The point of the brief is that it is cheap to read. Exercised at
    the realistic worst case (2026-09-16 fix wave, measured at 3512/4096):
    an archived epoch with programs already re-registered -- the longer
    "rebuilding" bullet, not the empty-board one -- and a verified epoch, so
    the Private Dungeons floor line is present too. The empty-store/
    no-verifier fixture used before this could never fail the budget it was
    meant to enforce.

    Raised 4096 -> 5120 on 2026-09-17 (D11): a behavioural test found four
    content gaps (safety model, `uv` bootstrap, first-run image pull,
    default hub) worth 616 bytes, and the design ruled that worth more
    than the margin -- nothing already in the brief was cut to pay for
    it. Worst case was 3703/4096 before those additions."""
    epoch = Epoch(secret_fingerprint=secret_fingerprint(SECRET),
                  arena_major=ARENA_MAJOR, seeds=HIDDEN_SEEDS)
    brief = render_brief(worst_case_store, epoch=epoch, version="9.9.9")
    assert len(brief.encode("utf-8")) <= 5120


def test_programs_pagination_is_documented(populated_store):
    """FIX7: this repo already shipped a read-one-page-as-the-whole-set bug
    once (the `total` key on every envelope exists because of it) -- say the
    page size and where the real count lives, not just "the registry"."""
    brief = render_brief(populated_store, version="9.9.9")
    assert "`limit`/`offset`" in brief
    assert "`total` on the envelope" in brief


def test_evolve_requirements_are_complete(populated_store):
    """FIX8: README's requirements table also lists a coding-agent CLI logged
    in on the host and `nethackers login` for `evolve`; an agent asked to
    "set this up for me" needs both, not just Docker or Podman."""
    brief = render_brief(populated_store, version="9.9.9")
    get_started = brief.split("## Get started", 1)[1].split("## How scoring works", 1)[0]
    assert "opencode2" in get_started
    assert "`nethackers login`" in get_started


def test_submit_requirements_are_complete(populated_store):
    """FIX8 follow-up (coordinator-flagged oversight, not deliberate triage):
    README's requirements table also lists `nethackers login` for `submit`,
    not just `gh` -- omitting it is the same failure mode FIX8 fixed for
    `evolve`: an agent wires up `gh` and stops, then `submit` still fails.
    """
    brief = render_brief(populated_store, version="9.9.9")
    requirements = " ".join(
        brief.split("## Get started", 1)[1].split("```bash", 1)[0].split()
    )
    assert "`submit` additionally needs `nethackers login` and `gh`" in requirements


def test_the_safety_model_is_stated(populated_store):
    """GAP 1 (2026-09-17 behavioural test; the most important of the four):
    the brief said nothing about `evolve` running a coding agent unattended
    with permission prompts disabled, or that every evaluated candidate --
    not only the improvements -- is pushed as public commits under the
    user's account. This document is instructions an autonomous agent acts
    on; a missing safety line causes real action on a real machine. Placed
    as a fourth "Read this first" bullet (D6): that section is what a
    truncating extraction model weights most."""
    brief = render_brief(populated_store, version="9.9.9")
    read_this_first = brief.split("## Read this first", 1)[1].split(
        "## State of the board", 1)[0]
    assert "permission prompts disabled" in read_this_first
    assert "unattended" in read_this_first
    assert "not only the improvements" in read_this_first
    assert "`nethacker`" in read_this_first
    assert "`--offline`" in read_this_first


def test_uv_bootstrap_is_explained(populated_store):
    """GAP 2: the brief's first command is `uv tool install nethackers` and
    said nothing about obtaining `uv` on a machine that lacks it -- a test
    agent went to Astral's docs and introduced a `curl | sh` this project
    never sanctioned. README's other path, `pip install nethackers`, and a
    pointer to Astral belong in "Get started" as prose near the command
    they qualify -- not a new fenced command the README-drift tripwire
    (test_every_command_in_the_brief_is_in_the_readme) would catch."""
    brief = render_brief(populated_store, version="9.9.9")
    get_started = brief.split("## Get started", 1)[1].split("## How scoring works", 1)[0]
    assert "`pip install nethackers`" in get_started
    assert "astral.sh/uv" in get_started


def test_setup_and_the_first_run_image_pull_are_in_get_started(populated_store):
    """GAP 3, updated for `nethackers setup` (2026-09-22): the brief names the
    one command that gets a machine ready and says how big the first pull is
    (about 1 GB, measured). It also tells a coding agent to propose the plan
    once instead of handing the user a to-do list (the observed failure)."""
    brief = render_brief(populated_store, version="9.9.9")
    get_started = brief.split("## Get started", 1)[1].split("## How scoring works", 1)[0]
    assert "nethackers setup" in get_started
    assert "about 1 GB" in get_started
    assert "doctor --pull" not in get_started
    assert "don't ask them to install things themselves" in get_started
    assert "nethackers setup --yes" in get_started


def test_the_default_hub_is_stated(populated_store):
    """GAP 4: the brief never said which hub the CLI talks to, so an agent
    handed a different hub's URL installs a CLI pointed somewhere else and
    does not notice. README states the default at the end of Quickstart;
    the brief now does the same at the end of "Get started"."""
    brief = render_brief(populated_store, version="9.9.9")
    get_started = brief.split("## Get started", 1)[1].split("## How scoring works", 1)[0]
    assert "https://nethackers.dunnolab.ai" in get_started
    assert "`--hub`" in get_started
    assert "`$NETHACKERS_HUB`" in get_started


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
