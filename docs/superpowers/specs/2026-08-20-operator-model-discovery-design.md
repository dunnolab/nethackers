# Operator model discovery + failure guard (design)

**Status:** Brainstormed & scoped 2026-08-20 (scope: *full live discovery for both operators*, user decision). Awaiting spec review before implementation. Origin: a run on a second machine whose `codex` was outdated (an unavailable model was selected) spun the whole iteration budget in seconds, re-emitting a "json body error" with no backoff.

## 1. Problem

Two independent gaps, both in `src/nethackers/harness/`, combined into one bad-UX failure:

1. **No availability check.** The tool/model picker is static. `harness/models.py:18-37` is a hand-maintained `MODELS` dict (the only source of truth), surfaced verbatim by the TUI form (`tui/screens/evolve_form.py:93-104`, options built in `_model_options()` `:144-148`) and the CLI (`cli.py:227-238`). Any model string is accepted with **zero validation** that the installed `codex`/`claude` can serve it. On the second machine, `codex` was older than the pinned model required, so every invocation was rejected server-side.

2. **No graceful failure.** The invocation and loop swallow the failure:
   - `harness/operator.py:50` spawns the agent with `stderr=subprocess.DEVNULL`, and `:79-80` reaps the process but **discards `returncode`**. A codex that fails instantly returns `OperatorResult(stopped_reason="completed")` with 0 tokens — indistinguishable from a healthy run.
   - The agent's own error line ("json body error") is streamed to the run log, then dropped by the token meter's `json.loads` guard (`harness/metering.py:44-47`).
   - The loop `harness/loop.py:142` is a bare `for k in range(iterations)`; every failure path is an immediate `continue` (gate reject `:190`, no-gain `:204`/`:221`, catch-all error `:239`) with **no sleep, no retry cap, no circuit-breaker**. A persistently-failing model burns the entire `iterations` budget in seconds.

## 2. What is actually possible (verified 2026-08-20)

The literal "list available models" is not in either CLI's `--help`, but both expose a real, cheap, **version-aware** path (probed live on this machine: codex-cli 0.146.0, Claude Code 2.1.237, both subscription-OAuth, no API keys):

- **codex → `codex debug models`.** Exit 0, stdout is one JSON doc `{"models":[{"slug", "display_name", "visibility", "supported_reasoning_levels":[…], "upgrade", …}]}`. Zero tokens; works offline (mirrored at `~/.codex/models_cache.json`, same shape). **The list is filtered by the CLI's own client version** — an old codex genuinely returns fewer/zero models (proven: the raw backend returns 8 models for 0.146.0, 0 for 0.50.0). This is exactly the reported bug made queryable: on the second machine this list would not have contained the pinned model. The server catalog is authoritative (a slug present in the binary's *bundled* list but absent from the server list is 400-rejected), so **never** use `codex debug models --bundled` for availability. Exclude `visibility == "hide"` (e.g. `codex-auto-review`).

- **claude → `GET https://api.anthropic.com/v1/models?limit=100`.** Headers `Authorization: Bearer <token>` + `anthropic-version: 2023-06-01`. HTTP 200, returns `data[].id` (plus `display_name`, `capabilities.effort.{low..max}.supported`). Free metadata endpoint, no tokens, no state mutation. The token is the CLI's stored OAuth access token. Unlike codex, an *old* `claude` still runs an unknown model name (only warns) — so for claude the gate is **account access**, not CLI age, and `/v1/models` reports exactly that.

## 3. Design

Three layers. Layer A is a new, self-contained capability module; B and C are integrations at existing seams. The loop, gate, eval, brief, and hermeticity flags are otherwise untouched.

### A. Discovery module — `src/nethackers/harness/discovery.py` (new)

Pure, dependency-injected, no import of TUI/CLI/loop. Uses stdlib `subprocess`/`json`/`platform` and the already-present `httpx`.

```python
@dataclass(frozen=True)
class ModelInfo:
    id: str                 # slug / model id passed to --model / -m
    label: str              # display_name for the picker
    reasoning: list[str]    # supported effort levels (for effort validation), may be []
    deprecated: bool        # codex "upgrade" != null

def list_models(backend, *, run=subprocess.run, http=httpx) -> list[ModelInfo] | None
def is_model_available(backend, model, *, models=None) -> bool | None
def detect_cli(backend, *, run=subprocess.run) -> CliInfo   # installed, version, logged_in
```

- **`list_models("codex")`** — `codex debug models` (timeout ~30s, any cwd). rc 0 → parse `models`, drop `visibility == "hide"`. rc≠0 / not-JSON (old CLI without the subcommand) → read `~/.codex/models_cache.json` if present (same shape). Neither → `None`.
- **`list_models("claude")`** — resolve token via the platform degrade chain (below), then `GET /v1/models`. 200 → `[ModelInfo(d["id"], d["display_name"], effort-caps, …)]`; paginate on `has_more`. **401 / network error → `None`** (stale token ≠ no access — never downgrade to "unavailable"). Alias set `{default, sonnet, opus, haiku, fable}` is always treated valid (the CLI resolves aliases server-side).
- **`is_model_available`** — `models = models or list_models(backend)`. `None` → return `None` (unknown). codex: exact slug membership. claude: alias-set → `True`; else strip a trailing `[1m]`, then membership.
- **Return-`None` contract:** `None` means "could not determine", and every caller treats it as *warn-and-proceed*, never as *block*. Only a confident `False` blocks.

**Claude credential lookup (`_claude_token()`), platform-aware, cross-machine (the cluster is Linux):**
1. macOS: `security find-generic-password -s "Claude Code-credentials" -w` → `.claudeAiOauth.accessToken` (Bearer).
2. Linux: `~/.claude/.credentials.json` → same field (Bearer).
3. Fallback: `ANTHROPIC_API_KEY` env → `x-api-key` header instead of Bearer.
4. None found → `list_models` returns `None`.
The token is read locally, used only for `/v1/models`, and **never logged**.

### B. Preflight gate

`preflight_model(backend, model) -> Decision(proceed|refuse|warn, message, cli, models)` in `discovery.py`, composed from `detect_cli` + `is_model_available`. Called once before `run_loop`, from `harness/launch.py` (headless CLI path) and mirrored by the TUI.

| Condition | Decision | User sees |
|---|---|---|
| binary not on PATH / not logged in | **refuse** | "codex not found / not logged in — install / `codex login`." |
| model confidently **absent** from live list | **refuse** | "codex `<version>` can serve: `<list>`. `<model>` isn't available — run `codex update` or pick one of these." |
| availability **unknown** (`None`: offline / old CLI / stale token) | **warn, proceed** | "Couldn't verify `<model>` on codex `<version>` — proceeding; the run will stop fast if it fails." |
| available | **proceed** | (nothing / a one-line "codex `<version>`, N models" note) |

CLI refusals print a clean, formatted message and exit non-zero (honoring the project's "beautiful + `--json`, never dump a traceback" rule — the existing top-level guard stays); with `--json` the decision is emitted structured.

### C. Surfacing — picker, CLI listing

- **TUI** (`tui/screens/evolve_form.py`): `_model_options(backend)` calls `discovery.list_models` **in a Textual worker/thread** (it does subprocess/HTTP, ~1–2 s) so the UI never blocks — show a "detecting…" placeholder, then populate the `Select` with live `ModelInfo` (label = `display_name`, "(deprecated)" suffix when flagged). `None` → fall back to the static `MODELS` with a subtle "(offline — curated list)" note. Keep the existing "Harness default" and "Custom…" entries. `harness/models.py` is demoted from source-of-truth to **fallback + display labels**.
- **CLI** (`cli.py`): new `nethackers models [--operator codex|claude] [--json]` that prints the live list for the current machine — directly answers "what can *this* computer serve?" without starting a run. Same discovery path.

### D. Runtime backstop (retained from the first design)

Catches what preflight cannot predict — token expiry mid-run, network, rate-limit, a server-side model pull.

- **`harness/operator.py`** — stop discarding failure signal: change `stderr=DEVNULL` → `PIPE`, drained by a second reader thread into a bounded deque (avoids the pipe-buffer deadlock, mirrors the existing stdout reader). Inspect `proc.returncode` at reap. `OperatorResult` gains `returncode: int | None` and `error_tail: str | None` (last few stderr lines, or the last error-shaped stdout line). Existing argv/hermeticity tests stay green — command construction is unchanged.
- **`harness/loop.py`** — classify each iteration:
  - **operator-error** = `returncode not in (0, None)` (primary, now available) **or** (0 tokens **and** no diff **and** sub-second wall time) (secondary heuristic).
  - **normal no-gain** = gate reject / no-improvement *after real token spend* (`gate.py:32-33` "child identical to parent" with tokens > 0). **Never counts as an error.**
  - Maintain `consecutive_errors`: on operator-error, `sleep(backoff)` (1s→2s→4s, capped; injected `sleep=` for tests, stop-event checked around it) then `+= 1`; on any non-error iteration, reset to 0. At `consecutive_errors >= threshold` (default **3**), **break** and emit a terminal diagnostic quoting `error_tail` + detected version + model + hint. A run that is merely *unlucky* (real work, no improvement) is unaffected.

## 4. Testing

All default-suite tests are offline, using the repo's existing fake-`popen` injection pattern plus a fake `http`/`run`:

- **discovery:** codex — fake `debug models` JSON parses & drops hidden; rc≠0 falls to a fake cache file; both-missing → `None`. claude — fake 200 → ids; **fake 401 → `None`** (not "unavailable"); alias-set always valid; `[1m]` stripped. Credential chain — keychain/creds-file/env each faked; none → `None`.
- **preflight decision table:** absent→refuse, `None`→warn+proceed, present→proceed, not-installed→refuse.
- **operator:** fake popen rc=1 + stderr lines → `returncode==1`, `error_tail` populated; rc=0 → clean. Argv tests unchanged.
- **loop:** N consecutive operator-error results → breaker trips at threshold, loop stops early, injected `sleep` shows backoff growth; N consecutive **normal no-gains** → does **not** trip (all iterations run).
- **gated live** (new markers, like `claude_live`): real `codex debug models` returns parseable JSON; real `/v1/models` returns 200 with the resolved token. Not in the default suite.

No new dependencies (`httpx` already required).

## 5. Cross-platform / cluster

The headless path (`nethackers evolve` under `awl run`, Linux) gets the same preflight refusal as the TUI — the highest-value place to catch a bad model before burning cluster time. codex discovery (`debug models` / cache file) is platform-agnostic; the only OS-specific code is the claude credential lookup (macOS Keychain vs Linux `~/.claude/.credentials.json`), covered by the degrade chain in §3A.

## 6. Residual risks (named honestly)

- **claude OAuth token against the public API.** `/v1/models` with the CLI's claude.ai OAuth token works today (200) but is slightly off the CLI's own path; the `anthropic-beta: oauth-2025-04-20` header is optional and changes nothing. Degrades safely: any non-200 → `None` → warn+proceed, so a future tightening never blocks a run.
- **Token TTL ~3 h.** A 401 is treated as unknown, not unavailable (§3A).
- **codex version-filter assumption.** We rely on `codex debug models` reflecting what the installed binary+account can serve. Verified for 0.50.0→0.146.0; if a future codex changes the contract, the confident-`False` path could wrongly refuse — mitigated because refusal names the served list and the `codex update` hint, and `Custom…` + warn-on-unknown remain escape hatches.
- **Keychain access.** Reading the CLI-created keychain item did not prompt in testing; if a hardened machine prompts, the read fails → `None` → warn+proceed.

## 7. Out of scope

- Auto-running `codex update` / re-auth (we detect and instruct, never mutate the user's tools).
- The undocumented `chatgpt.com/backend-api/codex/models` endpoint as a codex fallback (fragile, hand-extracted token) — `debug models` → cache file → `None` is sufficient.
- An upfront paid "positive probe" to confirm a good model — the enumerate-membership check plus the iteration-1 circuit-breaker already cover this for free.
