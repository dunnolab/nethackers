"""The program registry surface: solutions rows re-presented with the opaque
``id`` and a ``reference {repo, commit}`` object. ``root``/``entrypoint`` are
intentionally omitted -- the arena hard-codes ``bot.py`` at the repo root."""
from __future__ import annotations

from typing import Any

from nethackers.hub.ids import program_id
from nethackers.hub.store import Store

_COLUMNS = ("digest", "repo", "commit_sha", "owner", "registered_at")


def _row(values: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": program_id(values["digest"]),
        "owner": values["owner"],
        "reference": {"repo": values["repo"], "commit": values["commit_sha"]},
        "registered_at": values["registered_at"],
    }


def list_programs(store: Store, owner: str | None = None, limit: int = 50,
                  offset: int = 0) -> list[dict[str, Any]]:
    sql = f"SELECT {', '.join(_COLUMNS)} FROM solutions"
    params: list[Any] = []
    if owner is not None:
        sql += " WHERE owner = ?"
        params.append(owner)
    sql += " ORDER BY registered_at DESC LIMIT ? OFFSET ?"
    params += [limit, offset]
    rows = store.conn.execute(sql, params).fetchall()
    return [_row(dict(zip(_COLUMNS, r, strict=True))) for r in rows]


def get_program(store: Store, program_id_value: str) -> dict[str, Any] | None:
    digest = store.digest_for_program_id(program_id_value)
    if digest is None:
        return None
    row = store.get_solution(digest)
    if row is None:
        return None
    return _row(row)
