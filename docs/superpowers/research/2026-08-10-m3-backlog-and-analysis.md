# M3 — backlog + analysis (living doc, 2026-08-10)

Captured during the first manual-acceptance run of the evolve loop.

## Backlog (small fixes / improvements)
**Operator / reproducibility**
1. Pin the model — `--model` → `claude -p --model …` / `codex exec -m …`. `[S]`
2. Record full operator provenance per candidate — backend + model + **agent CLI version** (`claude/codex --version`) + harness git commit → lineage/evidence + verbose line. `[S]`

**CLI-UX / observability**
3. `evolve --json` / `-o json` — machine-readable per-iteration events. `[S]`
4. Agent heartbeat during `mutating…`. `[S–M]`
5. Route arena per-episode lines through the harness `report` (unified + timestamped) instead of raw stderr. `[S–M]`

**Robustness (deferred from review)**
6. P2 — operator silent-hang timeout (reader-thread / `select`). `[M]`
7. Cache parent dev-`Evidence` on `EliteState` (avoid re-eval each iteration). `[S–M]`

**Minor polish (accumulated deferred-minors)**
8. Friendlier errors at external seams (`hub.register`, `evaluate`, gate); digest `IndexError` guards (store/register); gate absent/malformed-manifest; brief empty-results; `evaluate` asserts `evaluator_image`; seeds single-char docstring. `[S each]`
