"""Hub objective catalog (M2a Task 4): the ~73 legal NetHack identities and
the frozen, deterministic ``(seed, character)`` batches published against
them, so tier-1 evidence is comparable across solutions.

## The identity set

An *identity* is an NLE character-build string ``role-race-align-gender``
(e.g. ``"val-dwa-law-fem"``). It is derived, not hand-typed, from three rule
tables that mirror NetHack 3.6.6's own validity data (upstream
``github.com/NetHack/NetHack``, tag ``NetHack-3.6.6_Released``,
``src/role.c``'s ``roles[]``/``races[]`` ``allow`` bitmasks -- read
directly from that source during development; cross-checked against
NetHack Wiki/community summaries, see below):

- ``ROLE_RACES`` / ``ROLE_ALIGNMENTS``: each role's *raw* allowed races and
  alignments (``roles[role].allow``'s race bits and align bits, kept
  separate exactly as the C source stores them -- e.g. Archeologist
  raw-allows ``{dwa, gno}`` for ``{law, neu}``).
- ``RACE_ALIGNMENTS``: each race's allowed alignments (``races[race].allow``
  align bits) -- the hard race->align invariant: dwarf->lawful, gnome->
  neutral, elf->chaotic, orc->chaotic, human->any of the three.
- ``ROLE_GENDERS``: each role's allowed genders. Every role allows both --
  **except Valkyrie, which NetHack restricts to female only.** In
  ``role.c``, the "Valkyrie" entry in ``roles[]`` carries ``ROLE_FEMALE``
  but never sets ``ROLE_MALE`` (every other role's entry sets both bits;
  every race in ``races[]`` also sets both gender bits, so gender is a
  pure role-level restriction). The game's own ``role_gendercount()``
  helper in ``role.c`` reads this exact bit to compute how many genders a
  role allows. This is NetHack's *only* role/race gender lock -- confirmed
  independently via web search of NetHack Wiki/community summaries
  ("The Valkyrie is the only gender-exclusive role in NetHack and is
  accessible only to female characters.").

``VALID[role]`` (a role's legal ``(race, align)`` pairs) is the
intersection of a role's raw allowed aligns with each candidate race's own
align invariant -- e.g. Archeologist raw-allows dwarf for ``{law, neu}``,
but dwarves are lawful-only, so only ``("dwa", "law")`` survives. This
reproduces exactly 38 valid ``(role, race, align)`` triples across the 13
roles. Crossing ``VALID`` with ``ROLE_GENDERS`` and formatting gives
``IDENTITIES``: 35 gender-unlocked triples x 2 genders = 70, plus
Valkyrie's 3 triples x 1 gender (female only) = 3, for exactly **73** --
matching TNNT's canonical "73 valid role/race/alignment/gender starting
combos" figure that motivated this design
(``docs/superpowers/research/2026-08-08-nethack-community-insights.md:68``).
See ``task-4-report.md`` for the full adjudication writeup (exact count,
sources, gender lock applied).

## Published batches

``build_catalog()`` returns the hub's objective catalog: ``"random"`` (a
broad, natural-weighted sample spanning many identities -- the north-star
headline objective), one ``"identity"`` objective per element of
``IDENTITIES`` (single-character, for per-identity boards), and ``"all"``
(a ``kind="functional"`` marker with an *empty* batch -- it owns no
episodes of its own; Task 9 computes it as a query over every identity
objective's atoms).

Every batch is HMAC-derived from ``PUBLIC_SECRET`` via
``nethackers.arena.seeds.trajectory_spec`` (the same mechanism
``arena/run.py`` uses for real evaluations), so batches are deterministic
and reproducible: rebuilding the catalog always yields byte-identical
``ObjectiveSpec``\\ s (see each spec's ``.digest()``).
"""

from __future__ import annotations

import random
from collections.abc import Sequence

from nethackers.arena.seeds import trajectory_spec
from nethackers.contracts.models import (
    DEFAULT_MAX_STEPS,
    DEFAULT_NO_PROGRESS_TIMEOUT,
    ObjectiveSpec,
)

# Matches M1's arena default (arena/run.py's `--secret` default is
# "public"; eval_batch hardcodes `--evaluation-id local`). This catalog's
# batches are meant to be public and reproducible by anyone, not secret --
# PUBLIC_SECRET only pins the HMAC so seeds are deterministic, not hidden.
PUBLIC_SECRET = "public"

# 120s = the LOCAL arena's per-action wall-clock HANG-GUARD. Its job is to
# catch a genuinely hung bot (NLE/AutoAscend can hang), NOT to enforce a
# scoring budget -- so it is set generous enough that a normal action, including
# a first-action cold numba JIT compile under parallel load, is never cut. Was
# 5.0 (M1); at 5.0, host contention cut normal AutoAscend actions (ESC fallback
# / bot_timeout) and corrupted the hub baseline. IMPORTANT: this is the LOCAL
# default only -- the held-out validator/verifier must choose its OWN
# action_timeout and must not inherit this value.
ACTION_TIMEOUT_SECONDS = 120.0

ROLES: tuple[str, ...] = (
    "arc", "bar", "cav", "hea", "kni", "mon", "pri",
    "ran", "rog", "sam", "tou", "val", "wiz",
)
RACES: tuple[str, ...] = ("hum", "elf", "dwa", "gno", "orc")
ALIGNMENTS: tuple[str, ...] = ("law", "neu", "cha")
GENDERS: tuple[str, ...] = ("fem", "mal")

# Race -> allowed alignments (races[race].allow's align bits, NetHack
# 3.6.6 src/role.c). The hard race->align invariant: every identity's
# (race, align) pair must satisfy this.
RACE_ALIGNMENTS: dict[str, frozenset[str]] = {
    "hum": frozenset({"law", "neu", "cha"}),
    "elf": frozenset({"cha"}),
    "dwa": frozenset({"law"}),
    "gno": frozenset({"neu"}),
    "orc": frozenset({"cha"}),
}

# Role -> raw allowed races (roles[role].allow's race bits, NetHack 3.6.6
# src/role.c), *before* filtering by RACE_ALIGNMENTS.
ROLE_RACES: dict[str, frozenset[str]] = {
    "arc": frozenset({"hum", "dwa", "gno"}),
    "bar": frozenset({"hum", "orc"}),
    "cav": frozenset({"hum", "dwa", "gno"}),
    "hea": frozenset({"hum", "gno"}),
    "kni": frozenset({"hum"}),
    "mon": frozenset({"hum"}),
    "pri": frozenset({"hum", "elf"}),
    "ran": frozenset({"hum", "elf", "gno", "orc"}),
    "rog": frozenset({"hum", "orc"}),
    "sam": frozenset({"hum"}),
    "tou": frozenset({"hum"}),
    "val": frozenset({"hum", "dwa"}),
    "wiz": frozenset({"hum", "elf", "gno", "orc"}),
}

# Role -> raw allowed alignments (roles[role].allow's align bits, NetHack
# 3.6.6 src/role.c), *before* filtering by RACE_ALIGNMENTS.
ROLE_ALIGNMENTS: dict[str, frozenset[str]] = {
    "arc": frozenset({"law", "neu"}),
    "bar": frozenset({"neu", "cha"}),
    "cav": frozenset({"law", "neu"}),
    "hea": frozenset({"neu"}),
    "kni": frozenset({"law"}),
    "mon": frozenset({"law", "neu", "cha"}),
    "pri": frozenset({"law", "neu", "cha"}),
    "ran": frozenset({"neu", "cha"}),
    "rog": frozenset({"cha"}),
    "sam": frozenset({"law"}),
    "tou": frozenset({"neu"}),
    "val": frozenset({"law", "neu"}),
    "wiz": frozenset({"neu", "cha"}),
}

# Role -> allowed genders. Every role allows both -- except Valkyrie,
# NetHack's sole gender-locked role (female only; see module docstring).
ROLE_GENDERS: dict[str, frozenset[str]] = {role: frozenset(GENDERS) for role in ROLES}
ROLE_GENDERS["val"] = frozenset({"fem"})

# Role -> legal (race, align) pairs: each role's raw allowed aligns,
# intersected per-race with that race's own align invariant. 38 pairs total
# across the 13 roles (matches NetHack 3.6's 38 valid (role,race,align)
# triples).
VALID: dict[str, frozenset[tuple[str, str]]] = {
    role: frozenset(
        (race, align)
        for race in ROLE_RACES[role]
        for align in ROLE_ALIGNMENTS[role] & RACE_ALIGNMENTS[race]
    )
    for role in ROLES
}

# The ~73 legal identities, sorted for a stable, reproducible order: 35
# gender-unlocked (role, race, align) triples x 2 genders, plus Valkyrie's
# 3 triples x 1 gender (female only) = 73.
IDENTITIES: tuple[str, ...] = tuple(
    sorted(
        f"{role}-{race}-{align}-{gender}"
        for role in ROLES
        for race, align in VALID[role]
        for gender in sorted(ROLE_GENDERS[role])
    )
)


def _natural_character(trajectory_id: int) -> str:
    """A deterministic, natural-weighted ``(role, race, align, gender)``
    draw for the ``"random"`` objective's batch -- always a member of
    ``IDENTITIES``.

    Role-first draw approximates NLE's natural random-character selection;
    exact NLE weighting is parked (M2a). Deterministic via the
    public-secret HMAC (``trajectory_spec``), so a given ``trajectory_id``
    always yields the same character -- the ``"random"`` batch is a
    frozen, reproducible sample, not a fresh roll per run. Gender is drawn
    from ``ROLE_GENDERS[role]`` (not an unconditional 50/50 coin flip) so a
    natural draw can never land on an illegal identity such as a male
    Valkyrie.
    """
    core_seed = trajectory_spec(PUBLIC_SECRET, "catalog:random", trajectory_id).core_seed
    rng = random.Random(core_seed)
    role = rng.choice(ROLES)  # uniform over the 13 roles
    race, align = rng.choice(sorted(VALID[role]))  # uniform over that role's (race,align)
    gender = rng.choice(sorted(ROLE_GENDERS[role]))  # uniform over that role's allowed genders
    return f"{role}-{race}-{align}-{gender}"


def build_catalog(
    *, random_size: int = 32, per_identity_size: int = 15
) -> dict[str, ObjectiveSpec]:
    """Build the hub's objective catalog.

    - ``"random"``: a broad, natural-weighted sample of ``random_size``
      episodes spanning many identities -- the north-star headline
      objective (``kind="random"``, ``aggregation="asc_median_mean"``).
    - one ``"identity"`` objective per ``IDENTITIES`` element:
      single-character, ``per_identity_size`` episodes each
      (``kind="identity"``, ``aggregation="mean"``).
    - ``"all"``: a ``kind="functional"`` marker with no episodes of its
      own (``batch=()``) -- Task 9 computes it as a query over every
      identity objective's atoms.

    ``random_size``/``per_identity_size`` are the published-batch sizes
    for this M2a default catalog (tunable, not architectural): 15 (was 8,
    M1's ``public-8`` default seed-set size) roughly halves the per-identity
    standard error -- cheap now that eval runs in parallel, while still a
    pragmatic fraction of the sibling arena's 1024-episode reliable setting
    (M3 SELECT-from-hub).

    Deterministic: every batch is derived from ``PUBLIC_SECRET`` via
    ``trajectory_spec``, so calling this twice with the same arguments
    always returns byte-identical ``ObjectiveSpec``\\ s (same
    ``.digest()`` per name).
    """
    catalog: dict[str, ObjectiveSpec] = {
        "random": ObjectiveSpec(
            name="random",
            kind="random",
            batch=tuple((i, _natural_character(i)) for i in range(random_size)),
            max_steps=DEFAULT_MAX_STEPS,
            no_progress_timeout=DEFAULT_NO_PROGRESS_TIMEOUT,
            action_timeout_seconds=ACTION_TIMEOUT_SECONDS,
            aggregation="asc_median_mean",  # north star: ascensions -> median -> mean
        )
    }

    for identity in IDENTITIES:
        catalog[identity] = ObjectiveSpec(
            name=identity,
            kind="identity",
            batch=tuple((i, identity) for i in range(per_identity_size)),
            max_steps=DEFAULT_MAX_STEPS,
            no_progress_timeout=DEFAULT_NO_PROGRESS_TIMEOUT,
            action_timeout_seconds=ACTION_TIMEOUT_SECONDS,
            aggregation="mean",
        )

    catalog["all"] = ObjectiveSpec(
        name="all",
        kind="functional",
        batch=(),  # owns no episodes -- a query over every identity's atoms (Task 9)
        max_steps=DEFAULT_MAX_STEPS,
        no_progress_timeout=DEFAULT_NO_PROGRESS_TIMEOUT,
        action_timeout_seconds=ACTION_TIMEOUT_SECONDS,
        aggregation="asc_median_mean",
    )

    return catalog


def build_union_spec(identities: Sequence[str], *, name: str) -> ObjectiveSpec:
    """A generalist objective's dev spec: the concatenation of each member
    identity's PUBLISHED per-identity batch (``CATALOG[ident].batch``), in
    sorted-identity order. kind='set', mean aggregation. No CATALOG entry is
    created -- this spec is built on demand for the evolve loop; wins are
    registered as per-identity slices.

    Concatenating the published batches (rather than rebuilding via
    ``range(per_identity_size)``) makes each member's slice structurally
    equal to ``CATALOG[ident].batch`` -- it can't drift from the catalog even
    if the catalog's own per-identity size ever changes."""
    batch = tuple(pair for ident in sorted(identities) for pair in CATALOG[ident].batch)
    return ObjectiveSpec(
        name=name,
        kind="set",
        batch=batch,
        max_steps=DEFAULT_MAX_STEPS,
        no_progress_timeout=DEFAULT_NO_PROGRESS_TIMEOUT,
        action_timeout_seconds=ACTION_TIMEOUT_SECONDS,
        aggregation="mean",
    )


CATALOG: dict[str, ObjectiveSpec] = build_catalog()
