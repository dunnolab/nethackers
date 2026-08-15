# Token Metering Refactor — Design

**Status:** approved design, pending implementation plan
**Date:** 2026-08-15

## Goal

Replace the ad-hoc, half-broken per-backend token counters with one
**principled, faithful token meter** shared by claude and codex — cache reads
and writes included — and **remove token/timeout governance entirely**. The
meter is pure observability; the operator runs to natural completion and is
stopped manually (hard kill).

## Background — what's broken today

- **claude undercounts:** `_claude_tokens` sums only `input_tokens +
  output_tokens`, ignoring `cache_read_input_tokens` /
  `cache_creation_input_tokens` — which are ~98% of the real throughput. The
  live bar crawled (measured 307 vs ~2.5M mid-run).
- **codex reads a field that doesn't exist:** `_codex_tokens` sums
  `total_tokens`, which is nowhere in codex-cli's schema → 0 tokens.
- **The counter is coupled to a budget cap** (`run_with_token_budget`:
  `total += tokens_from_line(line); if total >= token_budget: break`) that fed
  the live display, the metrics record, *and* enforcement at once.
- **`--timeout` is line-gated, not wall-clock:** checked only after a stdout
  line arrives, so it overshoots on silent operations and can't interrupt a
  true hang.

## Key finding — the schema is already unified

Both backends emit a final `result` line whose usage carries the **same four
fields** (verified on real logs):

```
codex  result usage:  input 523    output 86,663  cache_creation 202,975  cache_read 13,337,756
claude result usage:  input 165    output 80,820  cache_creation 184,264  cache_read 11,137,481
```

Both also carry `output_tokens_details.thinking_tokens` (a subset of output)
and an `iterations[]` per-turn breakdown. So the **final total is a single
unified read**. The only asymmetry is *streaming*: claude emits per-turn usage
on every `assistant` message (a live-climbing count); codex emits usage **only**
on the final `result` line (nothing per-turn).

## Design

### 1. `TokenUsage` — the normalized ground truth

```python
@dataclass(frozen=True)
class TokenUsage:
    input: int = 0
    output: int = 0          # includes thinking tokens
    cache_creation: int = 0
    cache_read: int = 0
    def __add__(self, other): ...            # component-wise
    @property
    def total(self) -> int:                  # faithful sum of all four
        return self.input + self.output + self.cache_creation + self.cache_read
```

Field extraction from a usage dict is **unified** (both backends use the same
keys) and null-safe — the one place that reads
`input_tokens`/`output_tokens`/`cache_read_input_tokens`/`cache_creation_input_tokens`.

### 2. Per-backend classification: increment vs. total

Each usage-bearing line is either an **incremental delta** (add) or a
**cumulative total** (authoritative — replace). This is the only
backend-specific knowledge:

- **claude:** `assistant` message usage → *increment*; `result` usage → *total*.
- **codex:** `result` usage → *total*; item/turn events carry no usage.

```python
def classify(backend, line) -> tuple[Literal["inc","total"], TokenUsage] | None
```

### 3. `Meter` — accumulate increments, snap to the authoritative total

```python
class Meter:
    def __init__(self, backend): self._backend, self._usage = backend, TokenUsage()
    def observe(self, line):
        c = classify(self._backend, line)
        if c is None: return
        kind, u = c
        self._usage = self._usage + u if kind == "inc" else u   # total replaces
    @property
    def usage(self) -> TokenUsage: return self._usage
```

- **claude:** climbs per-turn during the run, then the `result` line **replaces**
  the approximate running sum with the authoritative cumulative.
- **codex:** stays `TokenUsage()` until the `result` line, then snaps to the
  total. (The final number is faithful for both; only the *live climb* is
  claude-only — see §6.)

### 4. `run_operator` — stream + meter, no governance

`run_with_token_budget` → **`run_operator(cmd, cwd, *, backend, on_line) ->
OperatorResult`**:

- Spawn the operator subprocess; stream stdout line-by-line feeding
  `meter.observe(line)` and `on_line(line)`; reap at EOF.
- **No** `token_budget`, **no** `timeout_s`, **no** watchdog, **no** budget/deadline
  branches. The agent runs until it exits.
- `OperatorResult` carries **`usage: TokenUsage`** (replacing the bare
  `tokens: int`; expose `.total` for existing readers) and
  `stopped_reason ∈ {"completed", "killed"}`.
- `stderr` handling is out of scope here, but keep it capturable rather than
  hard-wired to `DEVNULL` (a separate concern noted for follow-up).

### 5. Manual stop = hard kill, no orphans

The loop runs in the TUI worker thread; the operator subprocess must die
cleanly when the run is interrupted (Ctrl-C / TUI quit). Run the operator in
its **own process group** (`start_new_session=True`) and, on shutdown,
terminate the whole group (SIGTERM→SIGKILL) so the coding agent and any
children it spawned are not orphaned. `stopped_reason="killed"` when the run is
interrupted mid-operator.

### 6. Display & records

- **Status bar:** show the faithful metered usage with M-scale formatting
  (e.g. `11.4M tok`) — **no `/budget` denominator** (there is no budget). The
  TUI holds a `Meter` per iteration tag; `_apply_log` calls `meter.observe`;
  the bar renders `meter.usage.total`.
- **codex live:** `0 tok` until the run ends, then the total. The bar may show
  elapsed / #commands for codex as the live signal instead (display-only,
  optional).
- **Detail/table view (optional):** a components breakdown
  (`input / output / cache-write / cache-read`) so the ~98% cache-read reality
  is visible.
- **`metrics.jsonl`:** record the faithful total and the four components (was a
  single undercounted int).

### 7. Removals

- `cli.py`: `--token-budget`, `--timeout` args + their `run.json` fields + wiring.
- `loop.py`: `token_budget`, `timeout_s` params; the `budget {tok}` report text.
- `operator.py`: `run_with_token_budget`, `_usage_tokens`, `_claude_tokens`,
  `_codex_tokens`, `agent_tokens` (superseded by `TokenUsage`/`classify`/`Meter`).
- `status.py` / `EvolveConfig`: the `token_budget` field and the `/budget`
  rendering.

## Decisions & non-goals (with rationale)

- **No pricing / cost / `$` / "effective tokens".** The API price *ratios*
  (output 5×, cache-write 1.25–2×, cache-read 0.1×) are provider-specific
  (claude=Anthropic, codex=OpenAI), TTL-dependent (the logs use `ephemeral_1h`
  cache), change over time, and have **no authoritative fresh runtime source**
  (no pricing API — only human-readable pages). Baking them in would be stale
  and dishonest. The meter reports *tokens*, faithfully; cost interpretation is
  the reader's, out of scope.
- **No token budget.** It was ineffective (undercount → never fired; codex has
  no streamed usage to check) and coupled three concerns into one counter.
- **No timeout / watchdog.** Coding agents terminate on their own; a hung agent
  is stopped manually. Removing it drops the false-safety line-gated timer.
- **Greedy simplicity:** one `TokenUsage`, one `classify`, one `Meter`, one
  `run_operator`.

## Testing

- `TokenUsage`: component add; `.total`; null-safe extraction from a usage dict
  (missing/`null` fields → 0).
- `classify`: claude `assistant` → `("inc", …)`; claude/codex `result` →
  `("total", …)`; non-usage lines → `None`; malformed line → `None` (never raises).
- `Meter`: claude increments accumulate, then a `result` line **replaces** with
  the authoritative total (not additive); codex stays zero until `result`, then
  snaps; cache fields are included in every case.
- `run_operator`: streams every line to `on_line`; meters to the final total;
  returns `stopped_reason="completed"` at EOF; **no** budget/timeout params exist.
- Manual kill: interrupting mid-operator terminates the subprocess group and
  yields `stopped_reason="killed"` (fake `popen`).
- `status.py`: renders `11.4M tok` with no `/budget`; codex renders `0 tok` pre-result.
- `cli.py`: `--token-budget` / `--timeout` are gone (parse error if passed);
  `run.json` no longer carries them.
