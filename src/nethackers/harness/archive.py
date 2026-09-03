"""The MAP-Elites cell archive: one cell per identity in S, each holding the
best program on that identity (by its per-identity mean, ``aggregate.
per_identity_means``). Replaces the island champions -- illumination, not a
scalar hill-climb. Pure data; the loop (harness/loop.py) drives it."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from nethackers.contracts.models import Evidence
from nethackers.harness import aggregate

UNION = "union"   # sentinel cell key: the best full-coverage program by union_mean


@dataclass
class Cell:
    digest: str
    tree: Path
    score: float                       # the elite's per-identity mean on THIS cell
    dev_evidence: Evidence | None      # the elite's full union-batch results (for the brief)


class CellArchive:
    def __init__(self, identities: Sequence[str]) -> None:
        self.identities: tuple[str, ...] = tuple(identities)
        self.cells: dict[str, Cell] = {}
        self.union: Cell | None = None
        self._seed_digest: str | None = None

    def mark_seed(self, digest: str) -> None:
        """Record the cold-start seed digest so ``coverage`` can count cells
        that have since advanced past it."""
        self._seed_digest = digest

    def cell(self, identity: str) -> Cell:
        return self.cells[identity]

    def insert(self, digest: str, tree: Path, dev_evidence: Evidence) -> list[str]:
        """Slot ``digest`` into every identity cell it strictly improves, and
        into the union cell if it strictly improves the best full-coverage
        union mean. Returns the improved keys (identities, plus ``"union"``
        when the union cell moved). An empty/absent cell (score ``-inf``) is
        always improved, so the first insert seeds every cell."""
        means = aggregate.per_identity_means(dev_evidence.results)
        improved: list[str] = []
        for ident in self.identities:
            score = means.get(ident)
            if score is None:
                continue
            current = self.cells.get(ident)
            if current is None or score > current.score:
                self.cells[ident] = Cell(digest, tree, score, dev_evidence)
                improved.append(ident)

        # Union cell (multi-identity sets only): the best program by the mean
        # over the WHOLE union batch. Only full-coverage evidence qualifies --
        # cold-start inserts pass a sub-union slice (a champion scored on a
        # subset of identities), which must not seed or move the union cell.
        if len(self.identities) > 1 and set(self.identities).issubset(
                r.character for r in dev_evidence.results):
            u = aggregate.union_mean(dev_evidence.results)
            if self.union is None or u > self.union.score:
                self.union = Cell(digest, tree, u, dev_evidence)
                improved.append(UNION)
        return improved

    def coverage(self) -> tuple[int, int]:
        filled = sum(1 for c in self.cells.values() if c.digest != self._seed_digest)
        return filled, len(self.identities)
