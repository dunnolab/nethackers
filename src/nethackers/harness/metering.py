"""Faithful, cache-inclusive token metering shared by both operators. The
``result`` stream line (same schema for claude + codex) is the authoritative
cumulative total; claude also streams per-turn ``assistant`` increments. Never
raises on a stream line -- a malformed / unknown shape yields ``None``."""
from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class TokenUsage:
    input: int = 0
    output: int = 0            # includes thinking tokens
    cache_creation: int = 0
    cache_read: int = 0

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(self.input + other.input, self.output + other.output,
                          self.cache_creation + other.cache_creation,
                          self.cache_read + other.cache_read)

    @property
    def total(self) -> int:
        return self.input + self.output + self.cache_creation + self.cache_read


def _usage_from_dict(usage: object) -> TokenUsage:
    if not isinstance(usage, dict):
        return TokenUsage()

    def g(key: str) -> int:
        value = usage.get(key)
        return int(value) if isinstance(value, (int, float)) else 0

    return TokenUsage(g("input_tokens"), g("output_tokens"),
                      g("cache_creation_input_tokens"), g("cache_read_input_tokens"))


def classify(backend: str, line: str) -> tuple[str, TokenUsage] | None:
    """``("total", usage)`` for a ``result`` line (authoritative cumulative,
    both backends), ``("inc", usage)`` for a claude ``assistant`` turn, else
    ``None``. Never raises."""
    try:
        obj = json.loads(line)
    except (ValueError, RecursionError):
        return None
    if not isinstance(obj, dict):
        return None
    if obj.get("type") == "result" and isinstance(obj.get("usage"), dict):
        return "total", _usage_from_dict(obj["usage"])
    if backend == "claude" and obj.get("type") == "assistant":
        inner = obj.get("message")
        if isinstance(inner, dict) and isinstance(inner.get("usage"), dict):
            return "inc", _usage_from_dict(inner["usage"])
    # codex-cli (>=0.1x) reports usage once, on `turn.completed` -- it emits NO
    # `result` line, so without this codex tokens would stay 0 forever. Its
    # `input_tokens` already includes the cached portion (cached_input_tokens is
    # a discount, not an addend), and codex's cache keys don't match
    # _usage_from_dict's, so the (unmatched -> 0) cache fields yield a faithful
    # input+output total with no double-count. `codex exec` is single-turn, so
    # this fires once -- total-replace is correct.
    if (backend == "codex" and obj.get("type") == "turn.completed"
            and isinstance(obj.get("usage"), dict)):
        return "total", _usage_from_dict(obj["usage"])
    return None


class Meter:
    """Accumulates claude increments; a ``result`` total REPLACES the running
    sum with the authoritative cumulative. codex stays zero until its
    ``result``. Faithful: every read sums all four token components."""

    def __init__(self, backend: str) -> None:
        self._backend = backend
        self._usage = TokenUsage()

    def observe(self, line: str) -> None:
        classified = classify(self._backend, line)
        if classified is None:
            return
        kind, usage = classified
        self._usage = (self._usage + usage) if kind == "inc" else usage

    @property
    def usage(self) -> TokenUsage:
        return self._usage
