"""Hub SQLite store (M2a Task 5): the thin data layer over solutions,
atoms, lineage (+ the derived-view tables Tasks 7-8 populate).
Pure stdlib ``sqlite3`` -- no NLE, no Docker, no network.

Schema and method contracts are exactly per ``task-5-context.md``, as
amended by Task A3: the atoms table's dedup key is now
``UNIQUE(solution_digest, identity, seed)`` -- ``objective_digest`` is gone
from both ``atoms`` and ``baseline_atoms`` (after random/all's retirement,
Task A1, every identity has exactly one canonical objective, so ``identity``
alone is the key). The vestigial write-only ``objectives`` catalog table
(nothing ever read it) is dropped too -- ``init_schema`` sheds it via
``_migrate_drop_objectives_table``; the catalog now lives only in memory
(``hub.objectives.CATALOG``). ``evidence_digest``/``horizon`` (spec Sec5's
atoms columns) are omitted entirely: they aren't on ``Atom``, and aren't
needed for M2a reads (``horizon`` is derivable via the catalog's
``max_steps``). Parked for M2b if provenance/verification ever needs them.

This module is a thin data layer only: ``init_schema()`` provisions the
derived-view tables (``attainment``, ``attainment_holders``) but nothing
here ever writes to them -- Tasks 7-8 own that logic. ``elite_pool`` (Task
8's materialized top-k) is DROPPED by ``_migrate_drop_elite_pool``: Part 2
of the hub API redesign made ``/elites`` a live query straight over
``atoms`` instead, so there is nothing left to store or migrate into for
it.

``init_schema()`` also migrates a legacy (pre-A3) ``atoms``/``baseline_atoms``
still carrying ``objective_digest`` to the identity-keyed shape (Task A4,
``_migrate_drop_objective_digest``) -- a no-op on a fresh or
already-migrated DB.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any

from nethackers.arena_version import major_for
from nethackers.contracts.models import Atom
from nethackers.hub.ids import program_id

logger = logging.getLogger(__name__)

# The three verified tables' DDL, pulled out of _SCHEMA below and named so
# _migrate_add_arena_major can recreate exactly ONE renamed-away table at a
# time via a plain conn.execute() (see that function). conn.executescript()
# -- used for _SCHEMA as a whole everywhere else -- always issues an
# implicit COMMIT before it runs, which would silently end the migration's
# own explicit transaction and reopen the very stranded-``_old``-table
# failure mode that transaction exists to prevent.
_VERIFIED_ATOMS_DDL = """
CREATE TABLE IF NOT EXISTS verified_atoms (
    solution_digest TEXT NOT NULL,
    owner TEXT NOT NULL,
    tier TEXT NOT NULL,
    identity TEXT NOT NULL,
    seed INTEGER NOT NULL,
    progression REAL NOT NULL,
    milestone TEXT,
    ascended INTEGER NOT NULL,
    status TEXT NOT NULL,
    turns INTEGER NOT NULL,
    steps INTEGER NOT NULL,
    evaluator_image TEXT NOT NULL,
    secret_fingerprint TEXT NOT NULL,
    verifier_token_fingerprint TEXT NOT NULL,
    arena_major INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(solution_digest, identity, seed, secret_fingerprint, arena_major)
);
"""
_VERIFIED_BASELINE_ATOMS_DDL = """
CREATE TABLE IF NOT EXISTS verified_baseline_atoms (
    solution_digest TEXT NOT NULL,
    owner TEXT NOT NULL,
    tier TEXT NOT NULL,
    identity TEXT NOT NULL,
    seed INTEGER NOT NULL,
    progression REAL NOT NULL,
    milestone TEXT,
    ascended INTEGER NOT NULL,
    status TEXT NOT NULL,
    turns INTEGER NOT NULL,
    steps INTEGER NOT NULL,
    evaluator_image TEXT NOT NULL,
    secret_fingerprint TEXT NOT NULL,
    verifier_token_fingerprint TEXT NOT NULL,
    arena_major INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(identity, seed, secret_fingerprint, arena_major)
);
"""
_VERIFIED_ATTEMPTS_DDL = """
CREATE TABLE IF NOT EXISTS verified_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    solution_digest TEXT NOT NULL,
    secret_fingerprint TEXT NOT NULL,
    evaluator_image TEXT NOT NULL,
    arena_major INTEGER NOT NULL,
    verifier_token_fingerprint TEXT NOT NULL,
    status TEXT NOT NULL,
    failure_kind TEXT,
    message TEXT,
    identities_done INTEGER NOT NULL,
    at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

# Keyed by table name so _migrate_add_arena_major can look up exactly the
# one CREATE TABLE statement it needs after renaming that table away.
_VERIFIED_TABLE_DDL: dict[str, str] = {
    "verified_atoms": _VERIFIED_ATOMS_DDL,
    "verified_baseline_atoms": _VERIFIED_BASELINE_ATOMS_DDL,
    "verified_attempts": _VERIFIED_ATTEMPTS_DDL,
}

# The whole DDL (task-5-context.md), verbatim. CREATE TABLE IF NOT EXISTS
# throughout makes init_schema() idempotent.
_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS solutions (
    digest TEXT PRIMARY KEY,
    repo TEXT, commit_sha TEXT, owner TEXT, root TEXT, entrypoint TEXT,
    registered_at TEXT
);
CREATE TABLE IF NOT EXISTS atoms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    solution_digest TEXT NOT NULL REFERENCES solutions(digest),
    owner TEXT NOT NULL, tier TEXT NOT NULL, identity TEXT NOT NULL,
    seed INTEGER NOT NULL, progression REAL NOT NULL, milestone TEXT,
    ascended INTEGER NOT NULL, status TEXT NOT NULL,
    turns INTEGER NOT NULL, steps INTEGER NOT NULL, evaluator_image TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(solution_digest, identity, seed)
);
CREATE TABLE IF NOT EXISTS baseline_atoms (
    solution_digest TEXT NOT NULL,
    owner TEXT NOT NULL,
    tier TEXT NOT NULL,
    identity TEXT NOT NULL,
    seed INTEGER NOT NULL,
    progression REAL NOT NULL,
    milestone TEXT,
    ascended INTEGER NOT NULL,
    status TEXT NOT NULL,
    turns INTEGER NOT NULL,
    steps INTEGER NOT NULL,
    evaluator_image TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
{_VERIFIED_ATOMS_DDL}{_VERIFIED_BASELINE_ATOMS_DDL}{_VERIFIED_ATTEMPTS_DDL}
CREATE TABLE IF NOT EXISTS lineage (
    child_digest TEXT NOT NULL REFERENCES solutions(digest),
    parent_digest TEXT NOT NULL,         -- may be an external/base solution: NO FK
    kind TEXT NOT NULL CHECK(kind IN ('parent','influence')),
    PRIMARY KEY(child_digest, parent_digest, kind)
);
CREATE TABLE IF NOT EXISTS poll_votes (
    voter_id   TEXT PRIMARY KEY,           -- one prophecy per browser
    method     TEXT NOT NULL,
    timeline   TEXT NOT NULL,
    roles      TEXT NOT NULL DEFAULT '[]', -- JSON array of role keys
    xp         TEXT,                        -- nullable
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
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
"""

_SOLUTION_COLUMNS: tuple[str, ...] = (
    "digest", "repo", "commit_sha", "owner", "root", "entrypoint", "registered_at",
)

# The 12 Atom columns, in Atom's declared field order. insert_atoms and
# iter_atoms both key off this single list so the SQL column order and
# Atom.to_dict()/from_dict() can never drift apart.
_ATOM_COLUMNS: tuple[str, ...] = (
    "solution_digest", "owner", "tier", "identity",
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
        "solution_digest", "owner", "tier", "identity",
        "seed", "ascended", "status", "milestone",
    }
)

_VERIFIED_EXTRA_COLUMNS = (
    "secret_fingerprint", "verifier_token_fingerprint", "arena_major")
_ITER_VERIFIED_FILTER_KEYS = _ITER_ATOMS_FILTER_KEYS | {
    "secret_fingerprint", "evaluator_image", "verifier_token_fingerprint",
    "arena_major",
}

_ATTEMPT_COLUMNS = ("solution_digest", "secret_fingerprint", "evaluator_image",
                    "arena_major", "verifier_token_fingerprint", "status",
                    "failure_kind", "message", "identities_done", "at")


def _migrate_drop_objective_digest(conn: sqlite3.Connection) -> None:
    """Convert a legacy ``atoms``/``baseline_atoms`` (carrying the dropped
    ``objective_digest`` column) to the identity-keyed shape via
    ``INSERT OR IGNORE ... ORDER BY rowid`` into the freshly (re)created
    table. No-op when the column is already absent (fresh or
    already-migrated DB).

    For ``atoms``, whose new shape has a real ``UNIQUE(solution_digest,
    identity, seed)`` index, this collapses any duplicate row to the
    EARLIEST (lowest ``rowid`` / insertion order). ``baseline_atoms`` has no
    such unique key in either shape (``insert_baseline_atoms`` is
    deliberately "no dedup" -- see its docstring), so nothing collides:
    every row is carried over losslessly, just minus the column.

    Ordered by ``rowid`` rather than the ``id`` column: ``atoms``' legacy
    ``id INTEGER PRIMARY KEY`` *is* its rowid (sqlite aliases the two), but
    ``baseline_atoms`` has never had an ``id`` column at all, even in its
    legacy shape -- ``rowid`` is the only insertion-order column both
    tables have.
    """
    for table in ("atoms", "baseline_atoms"):
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
        if not cols or "objective_digest" not in cols:
            continue   # fresh/new-shape table -> nothing to migrate
        new_cols = [c for c in cols if c not in ("id", "objective_digest")]
        col_list = ", ".join(new_cols)
        with conn:
            conn.execute(f"ALTER TABLE {table} RENAME TO {table}_old")
            conn.executescript(_SCHEMA)   # recreates the new-shape table (IF NOT EXISTS)
            conn.execute(
                f"INSERT OR IGNORE INTO {table} ({col_list}) "
                f"SELECT {col_list} FROM {table}_old ORDER BY rowid"
            )
            conn.execute(f"DROP TABLE {table}_old")


def _migrate_drop_objectives_table(conn: sqlite3.Connection) -> None:
    """Drop the legacy write-only ``objectives`` table. After random/all's
    retirement + identity-keying, every identity has exactly one canonical
    objective (held in the in-memory ``CATALOG``), atoms key on ``identity``,
    and nothing reads this table -- it is pure vestige. ``IF EXISTS`` makes
    this a no-op on a fresh or already-migrated DB."""
    conn.execute("DROP TABLE IF EXISTS objectives")
    conn.commit()


def _migrate_drop_elite_pool(conn: sqlite3.Connection) -> None:
    """Drop the legacy materialized ``elite_pool`` table. Part 2 of the hub
    API redesign made ``/elites`` a live query straight over ``atoms``
    (``views.elites.read_elites``) -- nothing writes ``elite_pool`` any
    more, so an existing DB just sheds it. ``IF EXISTS`` makes this a no-op
    on a fresh or already-migrated DB."""
    conn.execute("DROP TABLE IF EXISTS elite_pool")
    conn.commit()


def _migrate_add_program_id(conn: sqlite3.Connection) -> None:
    """Additively add the indexed ``program_id`` column and backfill every
    row by pure function of its ``digest`` (which is the ``repo@commit``
    reference). Idempotent: the ADD is guarded on the column's absence and
    the backfill only touches rows still NULL, so re-running init_schema is
    a no-op once every row is stamped. sqlite has no sha256(), so the
    backfill runs in Python."""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(solutions)")]
    if "program_id" not in cols:
        conn.execute("ALTER TABLE solutions ADD COLUMN program_id TEXT")
    rows = conn.execute(
        "SELECT digest FROM solutions WHERE program_id IS NULL").fetchall()
    for (digest,) in rows:
        conn.execute("UPDATE solutions SET program_id = ? WHERE digest = ?",
                     (program_id(digest), digest))
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_solutions_program_id"
        " ON solutions(program_id)")
    conn.commit()


def _migrate_add_arena_major(conn: sqlite3.Connection) -> int:
    """Re-key the three verified tables from ``evaluator_image`` onto
    ``arena_major``, backfilling the major from each row's own digest.
    Returns the number of rows dropped as duplicates (the caller, currently
    only ``init_schema``, is expected to surface a non-zero count -- see its
    call site). No-op (returns 0) on a fresh or already-migrated DB.

    Rename-and-copy rather than ``ALTER TABLE ADD COLUMN``: sqlite cannot add
    a NOT NULL column without a default to a non-empty table, and cannot alter
    a UNIQUE constraint at all. Same shape as
    ``_migrate_drop_objective_digest``, but each table's rename, recreate,
    backfill and drop run inside ONE EXPLICIT transaction
    (``BEGIN IMMEDIATE`` ... ``with conn:``'s implicit commit/rollback),
    rather than relying on ``with conn:`` alone: sqlite3's default implicit
    transaction handling never opens a transaction for a bare DDL statement
    (``ALTER TABLE`` / ``CREATE TABLE`` / ``DROP TABLE``), and
    ``executescript()`` -- used everywhere else in this module for ``_SCHEMA``
    as a whole -- unconditionally COMMITs any open transaction before it runs.
    Combined, those two facts meant the old version's rename and its empty
    recreated table were ALREADY DURABLE before the insert loop even started,
    so a failure partway through the loop (disk full, ``database is locked``,
    a killed process) rolled back only the inserts, leaving the original data
    stranded in ``{table}_old`` behind a NEW, EMPTY ``{table}`` that already
    has the ``arena_major`` column -- which the guard below reads as
    "already migrated" and will happily skip forever. Explicitly opening the
    transaction before the rename (and recreating with a single
    ``conn.execute(_VERIFIED_TABLE_DDL[table])`` rather than
    ``executescript``, so nothing commits early) makes the whole per-table
    sequence atomic: any raise anywhere in it leaves that table exactly as it
    was found, under its original name, with nothing renamed or dropped.

    Belt-and-suspenders on top of that atomicity: if a ``{table}_old`` is
    ever found lying around at the top of an iteration, this refuses to
    proceed rather than silently trusting the shape guard. The explicit
    transaction above should make this unreachable through this function's
    own mid-run failures going forward, but a table by this name could still
    exist from a run of the pre-fix version against a real database, or from
    an operator's own out-of-band intervention -- and treating either as
    "nothing to do" would abandon whatever real evaluation data is sitting
    in it rather than surface the question to a human.

    Rows are carried over in ``rowid`` order through ``INSERT OR IGNORE``, so
    when pooling two digests into one major collides a cell, the EARLIEST row
    wins -- arbitrary but deterministic, and harmless given the two digests are
    declared to score alike (design D7).

    An unclassified digest raises instead of guessing a major: storing a row
    under a guessed major would silently place it on a board it was never
    measured for (design invariant I2).
    """
    dropped = 0
    for table in ("verified_atoms", "verified_baseline_atoms", "verified_attempts"):
        old_name = f"{table}_old"
        stranded = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (old_name,),
        ).fetchone()
        if stranded:
            raise RuntimeError(
                f"{old_name} already exists -- a previous arena_major "
                f"migration attempt on {table} did not finish cleanly. "
                f"Refusing to guess whether {table} already holds every row "
                f"from it; inspect {old_name} by hand before retrying."
            )

        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
        if not cols or "arena_major" in cols:
            continue   # fresh or already-migrated -> nothing to do
        carried = [c for c in cols if c != "id"]
        col_list = ", ".join(carried)
        rows = conn.execute(
            f"SELECT {col_list} FROM {table} ORDER BY rowid").fetchall()

        image_at = carried.index("evaluator_image")
        majors = []
        for row in rows:
            major = major_for(row[image_at])
            if major is None:
                raise ValueError(
                    f"{table} holds an unclassified evaluator_image "
                    f"{row[image_at]!r}; add it to ARENA_MAJOR_BY_DIGEST "
                    f"before migrating"
                )
            majors.append(major)

        insert_cols = ", ".join([*carried, "arena_major"])
        placeholders = ", ".join("?" for _ in range(len(carried) + 1))
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(f"ALTER TABLE {table} RENAME TO {old_name}")
            conn.execute(_VERIFIED_TABLE_DDL[table])   # this table only, not executescript
            for row, major in zip(rows, majors, strict=True):
                cur = conn.execute(
                    f"INSERT OR IGNORE INTO {table} ({insert_cols}) "
                    f"VALUES ({placeholders})",
                    (*row, major),
                )
                dropped += 1 - cur.rowcount
            conn.execute(f"DROP TABLE {old_name}")
    return dropped


class Store:
    """A sqlite3 data layer over the hub's schema, with ONE CONNECTION PER
    THREAD.

    It used to hold a single connection shared by every caller. FastAPI runs
    this package's sync (``def``) handlers in a worker threadpool, so that
    connection was used concurrently by many threads -- and ``sqlite3``
    caches prepared statements *per connection*, so two threads running the
    same SQL got the same statement object and clobbered each other's
    results. In practice that surfaced as ``fetchone()`` returning ``None``
    for a ``SELECT COUNT(*)`` (``read_stats`` crashing with ``'NoneType' is
    not subscriptable``), short ``zip()``s, and ``InterfaceError: bad
    parameter or other API misuse``. It needed no load to trigger: the
    website's boot fans out four parallel requests, so a single visitor
    could race it.

    Every connection is therefore thread-local and created on first use in
    that thread. Callers are unaffected: transactions here are always
    ``with self._conn:`` *within one method call*, and a request is served
    on one thread, so no transaction ever spans threads. The threadpool is
    bounded, so the number of connections is too.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        self._local = threading.local()

    @property
    def _conn(self) -> sqlite3.Connection:
        """This thread's connection, opened (and PRAGMA'd) on first use.

        ``journal_mode=WAL`` matters now that there are concurrent
        connections: under the default rollback journal a writer locks out
        every reader, so one ``/register`` would stall the whole site.
        ``busy_timeout`` makes a contended write wait rather than raise
        ``SQLITE_BUSY`` immediately."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self._db_path, check_same_thread=False)
            # sqlite defaults foreign keys OFF, and it's a per-connection
            # setting -- required for insert_atoms' FK integrity behavior.
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA busy_timeout = 5000")
            self._local.conn = conn
        return conn

    @property
    def conn(self) -> sqlite3.Connection:
        """This thread's live connection (PRAGMAs already set) -- the seam
        the derived views (Tasks 7-9) use to own their own SQL against the
        ``attainment``/``attainment_holders`` tables (plus the live
        ``/elites`` query straight over ``atoms``)."""
        return self._conn

    def init_schema(self) -> None:
        """Create every table (idempotent) -- including the derived-view
        tables, which Tasks 7-8 populate, not this class. Then migrate a
        legacy ``atoms``/``baseline_atoms`` (still carrying the dropped
        ``objective_digest`` column) to the identity-keyed shape -- a no-op
        on a fresh or already-migrated DB (Task A4) -- and drop the legacy
        ``elite_pool`` table (Part 2: ``/elites`` is now a live query, so an
        existing DB just sheds it; also a no-op once already dropped).
        Finally re-key the three verified tables from ``evaluator_image``
        onto ``arena_major``, backfilling each row's major from its own
        digest (again a no-op on a fresh or already-migrated DB). That last
        migration can pool rows that collide once re-keyed, dropping the
        later duplicate (see ``_migrate_add_arena_major``) -- a non-zero
        count is logged at WARNING rather than silently discarded, since
        this runs once, unattended, over irreplaceable evaluation data."""
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        _migrate_drop_objective_digest(self._conn)
        _migrate_drop_objectives_table(self._conn)
        _migrate_add_program_id(self._conn)
        _migrate_drop_elite_pool(self._conn)
        dropped = _migrate_add_arena_major(self._conn)
        if dropped:
            logger.warning(
                "arena_major migration pooled %d verified row(s) as "
                "duplicates once re-keyed off evaluator_image", dropped,
            )

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
            INSERT INTO solutions
                (digest, repo, commit_sha, owner, root, entrypoint, registered_at, program_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(digest) DO UPDATE SET
                repo = excluded.repo,
                commit_sha = excluded.commit_sha,
                owner = excluded.owner,
                root = excluded.root,
                entrypoint = excluded.entrypoint,
                registered_at = excluded.registered_at,
                program_id = excluded.program_id
            """,
            (digest, repo, commit_sha, owner, root, entrypoint, registered_at, program_id(digest)),
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

    def digest_for_program_id(self, program_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT digest FROM solutions WHERE program_id = ?", (program_id,)).fetchone()
        return row[0] if row is not None else None

    def iter_solutions(self) -> list[dict[str, Any]]:
        """Every registered solution, as ``{digest, repo, commit_sha}`` --
        the source Task 7's ``verify_candidates`` scans to find programs
        still lacking full verified coverage."""
        rows = self._conn.execute("SELECT digest, repo, commit_sha FROM solutions").fetchall()
        return [{"digest": d, "repo": r, "commit_sha": c} for d, r, c in rows]

    def random_owners(self, n: int) -> list[str]:
        """Up to ``n`` random distinct hacker handles -- the ``owner``s in
        ``atoms`` (real *scored* submissions), the SAME source the leaderboard's
        ``hacker_board`` reads. That is what keeps it honest without an allowlist:
        the AutoAscend baseline lives in the isolated ``baseline_atoms`` table,
        and any seed/root that sits only in ``solutions`` was never scored into
        ``atoms`` -- so neither can appear. Cheap: a distinct-owner sample."""
        if n <= 0:
            return []
        rows = self._conn.execute(
            "SELECT DISTINCT owner FROM atoms WHERE owner != '' ORDER BY RANDOM() LIMIT ?",
            (n,),
        ).fetchall()
        return [row[0] for row in rows]

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
        """Insert each atom, deduping on ``UNIQUE(solution_digest, identity,
        seed)``. Returns the count of rows actually inserted (0 for atoms
        that already existed). Raises ``sqlite3.IntegrityError`` if an
        atom's ``solution_digest`` doesn't reference an existing row: ``OR
        IGNORE`` suppresses the UNIQUE dedup conflict, but sqlite always
        enforces FOREIGN KEY violations as ABORT regardless of the
        statement's own conflict-resolution clause.

        Atomic per call: ``with self._conn:`` commits once, only after
        every atom in the batch has inserted (or been dedup-skipped)
        without error. If any atom raises partway through (e.g. the FK
        case above), the connection's context-manager protocol rolls back
        everything this call did and re-raises -- so a raised call never
        leaves earlier-in-this-call rows pending on the long-lived
        connection for some later, unrelated commit to flush (fix round 1:
        a trailing manual ``commit()`` with no rollback allowed exactly
        that leak).
        """
        inserted = 0
        with self._conn:
            for atom in atoms:
                values = atom.to_dict()
                cur = self._conn.execute(
                    _INSERT_ATOM_SQL, tuple(values[column] for column in _ATOM_COLUMNS)
                )
                inserted += cur.rowcount
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

    def upsert_poll_vote(
        self, voter_id: str, *, method: str, timeline: str, roles: list[str], xp: str | None
    ) -> None:
        """Insert this browser's prophecy, or replace it if the voter_id
        already voted (one prophecy per browser; re-vote is last-wins).
        created_at is preserved across replacements; roles is stored as a
        JSON array."""
        self._conn.execute(
            """
            INSERT INTO poll_votes (voter_id, method, timeline, roles, xp)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(voter_id) DO UPDATE SET
                method = excluded.method,
                timeline = excluded.timeline,
                roles = excluded.roles,
                xp = excluded.xp,
                updated_at = datetime('now')
            """,
            (voter_id, method, timeline, json.dumps(roles), xp),
        )
        self._conn.commit()

    def iter_poll_votes(self) -> list[dict[str, Any]]:
        """Every vote, anonymized (no voter_id, no timestamps) -- the read
        shape GET /poll serves and the client aggregates over."""
        rows = self._conn.execute(
            "SELECT method, timeline, roles, xp FROM poll_votes ORDER BY created_at"
        ).fetchall()
        return [
            {"method": m, "timeline": t, "roles": json.loads(r), "xp": x}
            for (m, t, r, x) in rows
        ]

    def _insert_atom_rows(self, table: str, atoms: list[Atom], *,
                          extra: tuple[Any, ...] = ()) -> int:
        """``INSERT OR IGNORE`` ``atoms`` into ``table``, appending ``extra``
        (the major/attribution columns the verified tables carry beyond
        ``_ATOM_COLUMNS``) to every row. Returns rows actually inserted --
        dedup on the table's own UNIQUE key makes a re-submit a no-op.

        ``table`` is spliced into the SQL, so it is always a module-level
        literal from this file, never caller input; values stay parametrized.
        """
        columns = _ATOM_COLUMNS + _VERIFIED_EXTRA_COLUMNS[: len(extra)]
        cols = ", ".join(columns)
        placeholders = ", ".join("?" for _ in columns)
        inserted = 0
        with self._conn:
            for atom in atoms:
                values = atom.to_dict()
                row = tuple(values[c] for c in _ATOM_COLUMNS) + extra
                cur = self._conn.execute(
                    f"INSERT OR IGNORE INTO {table} ({cols}) VALUES ({placeholders})", row
                )
                inserted += cur.rowcount
        return inserted

    def _iter_atom_rows(self, table: str, allowed: frozenset[str], fname: str,
                        filters: dict[str, Any]) -> list[Atom]:
        """Return ``table``'s rows as ``Atom``s, filtered by ``column=value``
        (AND'ed). Unknown keys raise rather than silently no-op into an
        unfiltered scan; only whitelisted column names reach the WHERE clause.
        """
        unknown = sorted(set(filters) - allowed)
        if unknown:
            raise ValueError(f"unknown {fname} filter key(s): {unknown}")

        sql = f"SELECT {_SELECT_ATOM_COLUMNS_SQL} FROM {table}"
        params = list(filters.values())
        if filters:
            sql += " WHERE " + " AND ".join(f"{column} = ?" for column in filters)

        atoms = []
        for row in self._conn.execute(sql, params).fetchall():
            values = dict(zip(_ATOM_COLUMNS, row, strict=True))
            values["ascended"] = bool(values["ascended"])
            atoms.append(Atom.from_dict(values))
        return atoms

    def insert_baseline_atoms(self, atoms: list[Atom]) -> int:
        """Insert AutoAscend's computed baseline atoms into the isolated
        ``baseline_atoms`` table (no dedup, no FKs -- AutoAscend owns no
        ``solutions`` row). Returns the number of rows inserted."""
        cols = ", ".join(_ATOM_COLUMNS)
        placeholders = ", ".join("?" for _ in _ATOM_COLUMNS)
        with self._conn:
            for atom in atoms:
                values = atom.to_dict()
                self._conn.execute(
                    f"INSERT INTO baseline_atoms ({cols}) VALUES ({placeholders})",
                    tuple(values[column] for column in _ATOM_COLUMNS),
                )
        return len(atoms)

    def iter_baseline_atoms(self, **filters: Any) -> list[Atom]:
        """Return baseline atoms matching every ``column=value`` filter
        (AND'ed). Same filter whitelist as ``iter_atoms``."""
        unknown = sorted(set(filters) - _ITER_ATOMS_FILTER_KEYS)
        if unknown:
            raise ValueError(f"unknown iter_baseline_atoms filter key(s): {unknown}")

        sql = f"SELECT {_SELECT_ATOM_COLUMNS_SQL} FROM baseline_atoms"
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

    def insert_verified_atoms(
        self, atoms: list[Atom], *, secret_fingerprint: str,
        verifier_token_fingerprint: str, arena_major: int
    ) -> int:
        """Insert verified atoms into the isolated ``verified_atoms`` table
        (dedup on UNIQUE key, no FKs). Returns the number of rows inserted.
        """
        return self._insert_atom_rows(
            "verified_atoms", atoms,
            extra=(secret_fingerprint, verifier_token_fingerprint, arena_major),
        )

    def iter_verified_atoms(self, **filters: Any) -> list[Atom]:
        """Return verified atoms matching every ``column=value`` filter
        (AND'ed). Filter keys include solution_digest, identity, seed, plus
        secret_fingerprint, evaluator_image, verifier_token_fingerprint.
        """
        return self._iter_atom_rows(
            "verified_atoms", _ITER_VERIFIED_FILTER_KEYS, "iter_verified_atoms", filters,
        )

    def insert_verified_baseline_atoms(
        self, atoms: list[Atom], *, secret_fingerprint: str,
        verifier_token_fingerprint: str, arena_major: int
    ) -> int:
        """Insert AutoAscend's hidden-seed floor into the isolated
        ``verified_baseline_atoms`` table (dedup on UNIQUE key, no FKs).
        Returns the number of rows inserted.

        Isolated from ``verified_atoms`` for the same reason ``baseline_atoms``
        is isolated from ``atoms``: AutoAscend is the reference floor, never a
        participant, and a separate table makes that structural rather than
        dependent on every reader remembering a ``tier`` filter.

        The UNIQUE key omits ``solution_digest`` (which ``verified_atoms``
        needs to separate participants) because this table has exactly one
        logical author -- ``(identity, seed, epoch)`` is its natural key, so a
        recompute under the same epoch is an idempotent no-op no matter which
        box submits it.
        """
        return self._insert_atom_rows(
            "verified_baseline_atoms", atoms,
            extra=(secret_fingerprint, verifier_token_fingerprint, arena_major),
        )

    def iter_verified_baseline_atoms(self, **filters: Any) -> list[Atom]:
        """Return hidden-seed baseline atoms matching every ``column=value``
        filter (AND'ed). Same filter whitelist as ``iter_verified_atoms``."""
        return self._iter_atom_rows(
            "verified_baseline_atoms", _ITER_VERIFIED_FILTER_KEYS,
            "iter_verified_baseline_atoms", filters,
        )

    def insert_verified_attempt(self, *, solution_digest, secret_fingerprint,
                                evaluator_image, arena_major, verifier_token_fingerprint,
                                status, failure_kind, message, identities_done, at):
        """Insert an audit record of a verification attempt into the
        append-only ``verified_attempts`` table. Each call appends a new row.
        """
        cols = ", ".join(_ATTEMPT_COLUMNS)
        placeholders = ", ".join("?" for _ in _ATTEMPT_COLUMNS)
        vals = (solution_digest, secret_fingerprint, evaluator_image,
                arena_major, verifier_token_fingerprint, status, failure_kind,
                message, identities_done, at)
        with self._conn:
            self._conn.execute(f"INSERT INTO verified_attempts ({cols}) VALUES "
                               f"({placeholders})", vals)

    def latest_verified_attempt(self, solution_digest, *, secret_fingerprint,
                                arena_major):
        """Return the most recent (highest id) verification attempt for this
        solution within this secret + arena major, or None. Keyed on the major
        rather than the digest, so an attempt recorded under one digest is
        still found by a node running another digest of the same major."""
        cols = ", ".join(_ATTEMPT_COLUMNS)
        row = self._conn.execute(
            f"SELECT {cols} FROM verified_attempts"
            " WHERE solution_digest = ? AND secret_fingerprint = ? AND"
            " arena_major = ? ORDER BY id DESC LIMIT 1",
            (solution_digest, secret_fingerprint, arena_major)).fetchone()
        return dict(zip(_ATTEMPT_COLUMNS, row, strict=True)) if row else None
