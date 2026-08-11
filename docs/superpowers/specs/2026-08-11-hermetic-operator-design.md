# Hermetic coding-agent operator (design)

**Status:** Approved 2026-08-10 brainstorming → 2026-08-11 design approved by user; implementation on branch `m3/hermetic-operator`. Origin: root-cause of the M3 evolve loop returning 0 held-out wins.

## 1. Problem

The M3 evolution loop (`nethackers evolve`) ran 10 iterations over AutoAscend and registered **zero** improvements. Root cause (confirmed on disk + in the evolve TUI log, 2026-08-11): the headless `claude -p` mutation **operator is not stateless**. Claude Code persists per-directory memory + session transcripts under `~/.claude/projects/<cwd-slug>/`, keyed by the worktree cwd. The harness reuses stable worktree paths (`~/.nethackers/evolve/work/iter-{k}`), so that store survives **across iterations and across whole evolve runs**.

The operator had written itself memories (e.g. `~/.claude/projects/-Users-vokneruk--nethackers-evolve-work-iter-0/memory/nethack-arena-autoascend-glue.md`) describing a specific "AutoAscend glue fix" (`panic_on_errors=True` + bounded mid-game restart), with a standing self-instruction: *"working copies arrive RESET to the Aug-8 baseline… always diff `arena_adapter.py` against these fixes and re-apply if missing."* So on each run it **recalled and restored its prior fix** instead of exploring — it misread the evolutionary "start from parent" as "my work got wiped." That is why all mutations were the same edit: recall, not stochastic convergence.

The eval path was cleared: dev/held-out mount the same mutated worktree, and dev seeds (0..7/0..31) never overlap held-out (1000..1007). This is purely an operator-isolation defect.

## 2. The contract

A mutation operator MUST be a pure function of `(parent tree, brief)`. The spec sealed the operator from the *evaluator* (no eval seeds); it missed sealing it from its **own past**. This change adds that second seal.

**Chosen approach: B — disable the state channels by flag** (user decision, 2026-08-11). Keep the real config so auth/trust "just work"; disable every stateful/contextual channel at invocation. (Approach A — an ephemeral config home per mutation — was considered and rejected to avoid an auth-carry + headless-onboarding spike.)

## 3. Design

Only `src/nethackers/harness/operator.py`'s command construction changes. The loop, gate, eval, brief, seeds, and TUI are untouched. The hermeticity flags are factored into small, named, unit-testable helpers shared by the operators.

### Claude (`ClaudeOperator`)

Today: `claude -p <brief> --output-format stream-json --verbose --permission-mode acceptEdits`.

Add:

| flag | closes |
|------|--------|
| `--settings '{"autoMemoryEnabled": false}'` | **the confirmed bug**: no read/write of `~/.claude/projects/<slug>/memory` |
| `--setting-sources project,local` | drops the *user* settings layer (hooks, user MCP config, user permissions). Worktree has neither project nor local settings → effectively none. Auth is in `~/.claude.json` (not a setting source) → survives. |
| `--strict-mcp-config` | zero MCP servers (no `--mcp-config` given) |
| `--no-session-persistence` | no transcripts to resume from |

`autoMemoryEnabled` is the real settings key (confirmed in the `claude` 2.1.227 binary; `autoMemoryDirectory` is the sibling path key). Auth is OAuth (token in `~/.claude.json`).

### Codex (`CodexOperator`)

Today: `codex exec <brief> --json --full-auto`. Add: `--ephemeral` (no session files persisted), `--ignore-user-config` (drop user config; *auth still uses `CODEX_HOME`*), `--ignore-rules` (drop user/project execpolicy rules). Codex has no auto-memory-recall feature and `codex exec` never auto-resumes, so it does not recall across runs today; these flags are consistency + hygiene (stop writing session/rollout files, drop inherited config/rules).

### Residual leak (named honestly)

With approach B, `~/.claude/CLAUDE.md` (currently empty) and `~/.claude/rules/*` (one benign cluster rule) may still load for claude — fully excluding those needs the fresh-config-home swap (approach A) we deliberately did not take. Accepted trade-off. Codex's `--ignore-rules` closes the analogous codex channel.

### Existing pollution

`autoMemoryEnabled:false` renders the already-written memories (`~/.claude/projects/*nethackers-evolve-work-*/memory/`) **inert** — they are neither read nor written. No destructive cleanup is performed automatically. An optional manual cleanup command is provided in the PR/summary for tidiness.

## 4. Testing (this is what proves the fix)

1. **Unit (argv, no subprocess):** `ClaudeOperator.run` emits a cmd containing all four flags with `autoMemoryEnabled:false`; `CodexOperator.run` emits `--ephemeral --ignore-user-config --ignore-rules`. Inject a fake `popen`, assert argv. Keep existing operator tests green.
2. **Regression (gated behind a `claude`/network marker, like `test_docker_smoke.py`):** run two real mutations in the *same* worktree path back-to-back; assert (a) no `memory/` dir appears under that worktree's `~/.claude/projects/<slug>/`, and (b) the second run's stdout contains no recall markers (`restored`, `prior iteration`, `previously-validated`). Reproduces the exact bug and proves it is gone; also catches a wrong `autoMemoryEnabled` key (memory dir would reappear).

## 5. Verification (before claiming done)

- `uv run pytest` (full non-network suite) green.
- A live hermeticity check with the real `claude` CLI (clean env — `CLAUDE_CODE_*` unset to mimic the standalone `nethackers` process): invoke `claude -p` with the four flags in a throwaway cwd and confirm (a) it succeeds → **auth survives the flag set**, (b) **no `memory/` dir** is created for that cwd slug. If `--setting-sources project,local` breaks auth, drop it — the other three flags still fix the confirmed bug.

## 6. Out of scope (separate specs)

- Seal `arena_adapter.py` / eval glue out of the mutable surface ("no mid-game restart").
- top-k retention + sampling at SELECT (archive-level diversity).

Both are recorded in project memory `m3-evolve-loop-stuck-diagnosis`.
