"""Statically-enforced ``ArenaBot`` structural conformance.

``tests/test_bot_protocol.py`` checks structural conformance at runtime
(``hasattr``) only. Because ``ArenaBot`` is a ``typing.Protocol``, mypy also
checks conformance *statically* at the ``_accepts(_Conforming())`` call
below: if ``_Conforming``'s ``reset``/``act`` signatures ever drift from
``ArenaBot``, ``uv run mypy src/nethackers tests`` fails even though nothing
here raises at runtime. The runtime call is a smoke test that the class
under static check actually exists and is instantiable -- the conformance
check itself is mypy's, not pytest's.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from nethackers.contracts.bot import ArenaBot


class _Conforming:
    def reset(self, initial_observation: Mapping[str, Any]) -> None: ...
    def act(self, observation: Mapping[str, Any]) -> int:
        return 0


def _accepts(_: ArenaBot) -> None: ...


def test_conforming_class_is_accepted_at_runtime() -> None:
    # The `-> None` return annotation is load-bearing, not decorative: mypy's
    # default check_untyped_defs=false skips the *body* of a fully
    # unannotated function, which would silently turn this into a no-op
    # static check (confirmed empirically -- an unannotated def here lets an
    # obviously-wrong statement through with zero mypy errors). Annotating
    # the return type is what makes mypy actually evaluate the call below.
    _accepts(_Conforming())  # runtime smoke; mypy statically verifies structural conformance
