"""Which rows a view reads, given a tier.

The hub stores two measurement regimes in two isolated table pairs:
``atoms``/``baseline_atoms`` (published seeds, self-reported) and
``verified_atoms``/``verified_baseline_atoms`` (hidden seeds, run by a trusted
verifier). A score from one regime is meaningless against a floor from the
other -- different seeds, so the Delta measures nothing.

``Source`` exists to make that unlikely and require deliberate effort rather than
being a trap: a single Source instance hands out the atom table and its MATCHING
baseline table together, so the paired regime is the natural usage and the path
of least resistance. Defeating the pairing requires constructing two separate
Sources. A rotated secret, a re-pinned arena image, or a retired seed all drop
out here, once, instead of at four separate call sites. Later views that read
both atoms and baseline (such as ``views/recognition.py``) must resolve a single
Source and reuse it for both reads.

Note what is deliberately NOT routed through this module: ``GET /atoms``
returns raw per-atom rows including ``seed``. The hidden seeds are secret, so
that endpoint keeps reading ``atoms`` directly, where verified rows
structurally cannot appear.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from nethackers.contracts.models import Atom
from nethackers.hub.store import Store

VERIFIED = "verified"


class VerificationUnavailable(Exception):
    """A verified read was asked for on a hub with no verifier configured."""


@dataclass(frozen=True)
class Epoch:
    """The scope one verified measurement is comparable within. Two atoms are
    comparable only if all three match -- a rotated secret or a re-pinned arena
    starts a new epoch, and retired seeds fall out of the current one."""

    secret_fingerprint: str
    evaluator_image: str
    seeds: tuple[int, ...]


@dataclass(frozen=True)
class Source:
    """One tier's rows. Built by ``source_for``, not constructed directly by
    a view in practice -- nothing in the type stops it, but ``source_for`` is
    the natural, least-effort path, so the table pairing stays a strong
    default rather than an impossibility."""

    atoms_table: str
    tier: str
    epoch: Epoch | None

    def where(self, alias: str = "") -> tuple[str, tuple[Any, ...]]:
        """The SQL predicate selecting this tier's rows, and its parameters.
        Valid against the atoms table only; ``iter_baseline_atoms`` handles the
        baseline table's predicate. ``alias`` prefixes every column for queries
        that alias the table."""
        prefix = f"{alias}." if alias else ""
        if self.epoch is None:
            return f"{prefix}tier = ?", (self.tier,)
        if not self.epoch.seeds:
            # "seed IN ()" is a sqlite syntax error. An epoch with no seeds
            # matches nothing -- say so in SQL rather than raising mid-request.
            return "1 = 0", ()
        placeholders = ", ".join("?" for _ in self.epoch.seeds)
        return (
            f"{prefix}secret_fingerprint = ? AND {prefix}evaluator_image = ? "
            f"AND {prefix}seed IN ({placeholders})",
            (self.epoch.secret_fingerprint, self.epoch.evaluator_image,
             *self.epoch.seeds),
        )

    def iter_atoms(self, store: Store, **filters: Any) -> list[Atom]:
        """This tier's participant atoms, epoch-filtered."""
        if self.epoch is None:
            return store.iter_atoms(tier=self.tier, **filters)
        return self._epoch_rows(
            store.iter_verified_atoms(
                secret_fingerprint=self.epoch.secret_fingerprint,
                evaluator_image=self.epoch.evaluator_image,
                **filters,
            )
        )

    def iter_baseline_atoms(self, store: Store, **filters: Any) -> list[Atom]:
        """This tier's AutoAscend floor -- always the table that pairs with
        ``iter_atoms``. No ``tier`` filter: baseline rows carry
        ``tier="baseline"``, not the participant tier."""
        if self.epoch is None:
            return store.iter_baseline_atoms(**filters)
        return self._epoch_rows(
            store.iter_verified_baseline_atoms(
                secret_fingerprint=self.epoch.secret_fingerprint,
                evaluator_image=self.epoch.evaluator_image,
                **filters,
            )
        )

    def _epoch_rows(self, atoms: list[Atom]) -> list[Atom]:
        """Drop retired seeds. The store's filter whitelist supports only
        ``seed = ?`` equality, so the IN-list is applied here."""
        assert self.epoch is not None
        live = frozenset(self.epoch.seeds)
        return [atom for atom in atoms if atom.seed in live]


def source_for(tier: str, epoch: Epoch | None) -> Source:
    """Resolve a tier to its rows. Only ``"verified"`` is special-cased; every
    other value reads ``atoms`` filtered by that literal tier string, so an
    unknown tier returns no rows exactly as it does today rather than erroring.

    ``epoch`` is an explicit argument, not read from config, so a second epoch
    (public-seed verification, ``secret_fingerprint = sha256("public")``)
    becomes a caller change rather than a rewrite.
    """
    if tier == VERIFIED:
        if epoch is None:
            raise VerificationUnavailable("verification not configured")
        return Source(atoms_table="verified_atoms", tier=tier, epoch=epoch)
    return Source(atoms_table="atoms", tier=tier, epoch=None)
