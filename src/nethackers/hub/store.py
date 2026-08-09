"""Hub SQLite store (M2a Task 5): the thin data layer over solutions,
objectives, atoms, lineage (+ the derived-view tables Tasks 7-8 populate).
Pure stdlib ``sqlite3`` -- no NLE, no Docker, no network.

Schema and method contracts are exactly per ``task-5-context.md``. Two
deliberate spec-vs-``Atom`` reconciliations (that file's Resolution A):

- The atoms table's dedup key is ``UNIQUE(solution_digest, objective_digest,
  seed)``, not the brief's ``(evidence_digest, trajectory_id)`` -- the
  Task-1 ``Atom`` dataclass has neither field, and ``seed`` already serves
  as the trajectory id within a published batch.
- ``evidence_digest``/``horizon`` (spec Sec5's atoms columns) are omitted
  entirely: they aren't on ``Atom``, and aren't needed for M2a reads
  (``horizon`` is derivable via ``objectives.max_steps``). Parked for M2b
  if provenance/verification ever needs them.

This module is a thin data layer only: ``init_schema()`` provisions the
derived-view tables (``attainment``, ``attainment_holders``, ``elite_pool``)
but nothing here ever writes to them -- Tasks 7-8 own that logic.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from nethackers.contracts.models import Atom, ObjectiveSpec

# The whole DDL (task-5-context.md), verbatim. CREATE TABLE IF NOT EXISTS
# throughout makes init_schema() idempotent.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS solutions (
    digest TEXT PRIMARY KEY,
    repo TEXT, commit_sha TEXT, owner TEXT, root TEXT, entrypoint TEXT,
    registered_at TEXT
);
CREATE TABLE IF NOT EXISTS objectives (
    objective_digest TEXT PRIMARY KEY,
    name TEXT UNIQUE NOT NULL, kind TEXT NOT NULL, aggregation TEXT NOT NULL,
    max_steps INTEGER NOT NULL, no_progress_timeout INTEGER NOT NULL,
    action_timeout_seconds REAL NOT NULL,
    batch TEXT NOT NULL                  -- JSON [[seed, character], ...]
);
CREATE TABLE IF NOT EXISTS atoms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    solution_digest TEXT NOT NULL REFERENCES solutions(digest),
    objective_digest TEXT NOT NULL REFERENCES objectives(objective_digest),
    owner TEXT NOT NULL, tier TEXT NOT NULL, identity TEXT NOT NULL,
    seed INTEGER NOT NULL, progression REAL NOT NULL, milestone TEXT,
    ascended INTEGER NOT NULL, status TEXT NOT NULL,
    turns INTEGER NOT NULL, steps INTEGER NOT NULL, evaluator_image TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(solution_digest, objective_digest, seed)
);
CREATE TABLE IF NOT EXISTS lineage (
    child_digest TEXT NOT NULL REFERENCES solutions(digest),
    parent_digest TEXT NOT NULL,         -- may be an external/base solution: NO FK
    kind TEXT NOT NULL CHECK(kind IN ('parent','influence')),
    PRIMARY KEY(child_digest, parent_digest, kind)
);
-- derived views (schema only here; Tasks 7-8 populate)
CREATE TABLE IF NOT EXISTS attainment (
    identity TEXT NOT NULL, milestone TEXT NOT NULL,
    first_solution TEXT NOT NULL, first_owner TEXT NOT NULL, first_at TEXT NOT NULL,
    PRIMARY KEY(identity, milestone)
);
CREATE TABLE IF NOT EXISTS attainment_holders (
    identity TEXT NOT NULL, milestone TEXT NOT NULL,
    solution_digest TEXT NOT NULL, owner TEXT NOT NULL, reached_at TEXT NOT NULL,
    PRIMARY KEY(identity, milestone, solution_digest)
);
CREATE TABLE IF NOT EXISTS elite_pool (
    identity TEXT NOT NULL, solution_digest TEXT NOT NULL,
    score REAL NOT NULL, rank INTEGER NOT NULL,
    PRIMARY KEY(identity, solution_digest)
);
"""

_SOLUTION_COLUMNS: tuple[str, ...] = (
    "digest", "repo", "commit_sha", "owner", "root", "entrypoint", "registered_at",
)

# The 13 Atom columns, in Atom's declared field order. insert_atoms and
# iter_atoms both key off this single list so the SQL column order and
# Atom.to_dict()/from_dict() can never drift apart.
_ATOM_COLUMNS: tuple[str, ...] = (
    "solution_digest", "objective_digest", "owner", "tier", "identity",
    "seed", "progression", "milestone", "ascended", "status",
    "turns", "steps", "evaluator_image",
)

_INSERT_ATOM_SQL = "INSERT OR IGNORE INTO atoms ({}) VALUES ({})".format(
    ", ".join(_ATOM_COLUMNS), ", ".join("?" for _ in _ATOM_COLUMNS)
)
_SELECT_ATOM_COLUMNS_SQL = ", ".join(_ATOM_COLUMNS)

# iter_atoms(**filters) whitelist: real atoms columns only, so an unknown
# kwarg can't silently no-op into an unfiltered scan, and only known-safe
# column names are ever spliced into the WHERE clause (values stay
# parametrized).
_ITER_ATOMS_FILTER_KEYS: frozenset[str] = frozenset(
    {
        "solution_digest", "objective_digest", "owner", "tier", "identity",
        "seed", "ascended", "status", "milestone",
    }
)


class Store:
    """A single-connection sqlite3 data layer over the hub's schema.

    Thread-safety beyond ``check_same_thread=False`` is the API task's
    concern (task-5-context.md); a single connection is fine for M2a.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._conn: sqlite3.Connection = sqlite3.connect(db_path, check_same_thread=False)
        # sqlite defaults foreign keys OFF, and it's a per-connection
        # setting -- required for insert_atoms' FK integrity behavior.
        self._conn.execute("PRAGMA foreign_keys = ON")

    def init_schema(self) -> None:
        """Create every table (idempotent) -- including the derived-view
        tables, which Tasks 7-8 populate, not this class."""
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def upsert_solution(
        self,
        digest: str,
        *,
        repo: str,
        commit_sha: str,
        owner: str,
        root: str,
        entrypoint: str,
        registered_at: str,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO solutions (digest, repo, commit_sha, owner, root, entrypoint, registered_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(digest) DO UPDATE SET
                repo = excluded.repo,
                commit_sha = excluded.commit_sha,
                owner = excluded.owner,
                root = excluded.root,
                entrypoint = excluded.entrypoint,
                registered_at = excluded.registered_at
            """,
            (digest, repo, commit_sha, owner, root, entrypoint, registered_at),
        )
        self._conn.commit()

    def get_solution(self, digest: str) -> dict[str, Any] | None:
        columns_sql = ", ".join(_SOLUTION_COLUMNS)
        cur = self._conn.execute(
            f"SELECT {columns_sql} FROM solutions WHERE digest = ?", (digest,)
        )
        row = cur.fetchone()
        if row is None:
            return None
        return dict(zip(_SOLUTION_COLUMNS, row, strict=True))

    def objectives_upsert(self, spec: ObjectiveSpec) -> None:
        objective_digest = spec.digest()
        batch = json.dumps([[seed, character] for seed, character in spec.batch])
        self._conn.execute(
            """
            INSERT INTO objectives (
                objective_digest, name, kind, aggregation, max_steps,
                no_progress_timeout, action_timeout_seconds, batch
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(objective_digest) DO UPDATE SET
                name = excluded.name,
                kind = excluded.kind,
                aggregation = excluded.aggregation,
                max_steps = excluded.max_steps,
                no_progress_timeout = excluded.no_progress_timeout,
                action_timeout_seconds = excluded.action_timeout_seconds,
                batch = excluded.batch
            """,
            (
                objective_digest,
                spec.name,
                spec.kind,
                spec.aggregation,
                spec.max_steps,
                spec.no_progress_timeout,
                spec.action_timeout_seconds,
                batch,
            ),
        )
        self._conn.commit()

    def add_lineage(self, child: str, parent: str, kind: str) -> None:
        """``kind`` must be ``"parent"`` or ``"influence"`` (DB CHECK
        constraint enforces this). Idempotent: the composite primary key
        dedups a re-added ``(child, parent, kind)`` triple."""
        self._conn.execute(
            "INSERT OR IGNORE INTO lineage (child_digest, parent_digest, kind) VALUES (?, ?, ?)",
            (child, parent, kind),
        )
        self._conn.commit()

    def insert_atoms(self, atoms: list[Atom]) -> int:
        """Insert each atom, deduping on ``UNIQUE(solution_digest,
        objective_digest, seed)``. Returns the count of rows actually
        inserted (0 for atoms that already existed). Raises
        ``sqlite3.IntegrityError`` if an atom's ``solution_digest`` or
        ``objective_digest`` doesn't reference an existing row: ``OR
        IGNORE`` suppresses the UNIQUE dedup conflict, but sqlite always
        enforces FOREIGN KEY violations as ABORT regardless of the
        statement's own conflict-resolution clause.
        """
        inserted = 0
        for atom in atoms:
            values = atom.to_dict()
            cur = self._conn.execute(
                _INSERT_ATOM_SQL, tuple(values[column] for column in _ATOM_COLUMNS)
            )
            inserted += cur.rowcount
        self._conn.commit()
        return inserted

    def iter_atoms(self, **filters: Any) -> list[Atom]:
        """Return atoms matching every ``column=value`` filter (AND'ed).
        ``filters`` keys must be a subset of the real atoms columns listed
        in ``_ITER_ATOMS_FILTER_KEYS`` -- an unknown key raises rather than
        being silently ignored.
        """
        unknown = sorted(set(filters) - _ITER_ATOMS_FILTER_KEYS)
        if unknown:
            raise ValueError(f"unknown iter_atoms filter key(s): {unknown}")

        sql = f"SELECT {_SELECT_ATOM_COLUMNS_SQL} FROM atoms"
        params = list(filters.values())
        if filters:
            sql += " WHERE " + " AND ".join(f"{column} = ?" for column in filters)

        rows = self._conn.execute(sql, params).fetchall()
        atoms = []
        for row in rows:
            values = dict(zip(_ATOM_COLUMNS, row, strict=True))
            values["ascended"] = bool(values["ascended"])
            atoms.append(Atom.from_dict(values))
        return atoms
