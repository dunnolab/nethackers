"""The uniform list envelope: ``{generated_at, **context, rows}``.

``generated_at`` is the hub's authoritative as-of (kept deliberately -- see
the spec's envelope rationale: distributed writers + agent consumers with no
reliable request clock). Context keys are ``scope``/``tier``/``by``/``n``
etc.; a ``None`` context value is dropped so only applicable keys appear."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def envelope(rows: list[Any], **context: Any) -> dict[str, Any]:
    ctx = {k: v for k, v in context.items() if v is not None}
    return {"generated_at": datetime.now(UTC).isoformat(), **ctx, "rows": rows}
