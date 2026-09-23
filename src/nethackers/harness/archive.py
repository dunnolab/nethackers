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
        always improved, so the first insert seeds every cell.

        A program that TIES a cell and strictly improves somewhere else takes
        that cell too, without being reported as an improvement there. It
        dominates the incumbent -- equal here, better elsewhere -- and the cell
        exists to hand out parents, so holding the dominated bot hands the
        mutator a tree that is behind the archive on every other identity. That
        is not hypothetical: in run 20260922-224201 a change helped only the orc
        rogues and tied the human ones, so the human cells kept the older bot;
        three iterations in a row then drew a human cell, re-derived the orc
        change (the one visible way to reach the stated target), reproduced the
        champion exactly, and were rejected for improving nothing. The score
        did not move in those cells, so ``improved`` must not claim it did --
        only the occupant changes.
        """
        means = aggregate.per_identity_means(dev_evidence.results)
        improved: list[str] = []
        tied: list[str] = []
        for ident in self.identities:
            score = means.get(ident)
            if score is None:
                continue
            current = self.cells.get(ident)
            if current is None or score > current.score:
                self.cells[ident] = Cell(digest, tree, score, dev_evidence)
                improved.append(ident)
            elif score == current.score and current.digest != digest:
                tied.append(ident)

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

        if improved:   # it won somewhere, so it dominates every cell it tied
            for ident in tied:
                self.cells[ident] = Cell(digest, tree, means[ident], dev_evidence)
        return improved

    def coverage(self) -> tuple[int, int]:
        filled = sum(1 for c in self.cells.values() if c.digest != self._seed_digest)
        return filled, len(self.identities)
