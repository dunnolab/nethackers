# Mutator sandbox + live-env experimentation — design

**Status:** Approved in brainstorming 2026-08-16. Grounded by two research docs: [`../research/2026-08-16-coding-agent-sandboxing.md`](../research/2026-08-16-coding-agent-sandboxing.md) (how the harnesses sandbox; the container-vs-wrapper decision) and [`../research/2026-08-10-agentic-evolutionary-search-grounding.md`](../research/2026-08-10-agentic-evolutionary-search-grounding.md) (mutator-execution regime + eval-hacking record). Sequels deferred to their own specs (see §7).

## 1. Problem

The evolutionary loop's better-performing regime is an **agentic operator that executes code and experiments against the live environment** (grounding doc: Vesper/RHO beat single-completion; "quality of reasoning per candidate > number of generations"). We reproduced this locally: the `codex exec --full-auto` operator (which runs Python in its own sandbox) found improvements where `claude -p --permission-mode acceptEdits` (edits only, no execution) could not.

But today the mutator is the **least-isolated** component in the system, and it has **no live environment** to experiment in:

- **The operator runs unsandboxed on the host.** `run_operator` (`harness/operator.py:50`) launches the coding-agent CLI with `subprocess.Popen(..., start_new_session=True)` — a direct host process, no container, no resource limits, no filesystem/network confinement. The only containment is the CLI's own permission mode (`acceptEdits` / `--full-auto`) and a process-group hard-kill on stop.
- **The asymmetry is backwards.** The *evaluator* is already sandboxed (`eval/runner.py:154` — `docker run --rm --network none`, plus a spawn-subprocess bot sandbox in `arena/sandbox.py`); the *mutator* is not. Turning on mutator execution + a live env expands the code-execution surface on exactly the component with zero isolation.
- **No live NLE reaches the mutator.** NLE is compiled only into the arena image (`arena/Dockerfile`) and used post-hoc by the evaluator. The mutator edits blind and cannot test candidates against the env it will be scored in.

Hazards this must contain (in reproduced-severity order, per the grounding doc): **runaway/fork-bomb processes** (an RHO agent fork-bombed its host and killed the operator's SSH session), **out-of-workspace writes**, and the mutator **leaking into or gaming the sealed scorer** (DGM faked tool-use logs; Sakana's "150×" collapsed to harness exploits; Vesper measured an 8.2% hack rate from the strongest model). Constraints: **local personal machines only** (Apple-Silicon macOS + Linux — no cluster), **really lightweight**, and **harness-agnostic** (must wrap Codex, Claude Code, Pi, opencode, and future CLIs — not depend on each one's built-in sandbox).

## 2. Goal & scope

Give the mutator a **sandbox that is simultaneously the cage around the agent and the box holding a live Python + NLE it experiments in** — one uniform boundary, any harness, on a laptop — while keeping that experimentation physically walled off from the sealed scorer.

**Threat model: accident-grade.** Our own model's generated code, on our own machines, no secrets in reach. We defend against thrashing and accidental blast radius, not a determined adversary escaping a hardened kernel boundary. This rules *out* microVMs/gVisor (isolation we don't need, overhead we'd pay) and rules *in* plain containers.

**In scope:** a multi-arch NLE-baked image; a `ContainerOperator` backend that runs the whole harness CLI inside a per-run container with cgroup caps; the live-env experimentation channel; the scorer-wall-as-mount-topology; the mutator's training-tier-only information diet; the macOS/Linux host-runtime story.

**Out of scope (deferred, see §7):** network egress allow-listing (v1 uses open egress); a persistent Jupyter/IPython kernel server; warm-container pooling; gVisor/microVM tiers; and the loop's **feedback/lineage** changes (richer training diagnostics, distilled credit-assignment) — a separate spec.

## 3. Design

### 3.1 Why a container, not a process-wrapper

The featherweight option (an srt-style per-OS wrapper: bubblewrap on Linux, Seatbelt on macOS) is disqualified by two of our own requirements (full analysis in the sandboxing research doc, §H):

1. **No process-level sandbox has resource limits.** Seatbelt, bubblewrap, Landlock, srt, and Codex's native sandbox impose no CPU/memory/pids caps — a fork bomb inside them is still a fork bomb on the host. Fork-bomb/runaway is our **#1 hazard**; that defense lives only in **cgroups v2**, i.e. containers.
2. **NLE barely builds natively on Apple-Silicon macOS.** A wrapper would require a native NLE build on every Mac; a Linux container makes NLE a build-once, run-everywhere artifact.

Running the whole CLI inside a container is also the configuration both vendors explicitly bless for unattended use ("externally sandboxed" `--yolo` / `--dangerously-skip-permissions` as a non-root user in a container). It is the only shape that is simultaneously mac+Linux, harness-agnostic, resource-limited, and light.

### 3.2 Image layering (multi-arch)

Reuse and extend the existing layered build (`arena/Dockerfile` already isolates the NLE compile from `src/` edits):

```
nethackers/nle-base:<digest>     # shared: OS build deps + compiled NLE + gymnasium + Python env
  ├── nethackers/arena:<digest>  # scorer — EXISTING image; ENTRYPOINT python -m nethackers.arena.run
  └── nethackers/mutator:<digest># NEW — adds harness CLIs + non-root `agent` user; no fixed ENTRYPOINT
```

- Both scorer and mutator derive from **one NLE base layer** ⇒ NLE compiles once and the mutator experiments in byte-identical env to the scorer (parity for free).
- The mutator image adds the harness CLIs (`npm i -g @anthropic-ai/claude-code @openai/codex @earendil-works/pi-coding-agent opencode-ai`) and a non-root `agent` user (Claude Code refuses `--dangerously-skip-permissions` as root; the `agent` user also owns the bind-mounted workspace via a PUID/PGID entrypoint shim).
- Built `--platform linux/arm64,linux/amd64`. CI smoke test both arches: `python -c "import nle, gymnasium; gymnasium.make('NetHackChallenge-v0').reset()"`.

### 3.3 Per-run container invocation

Each mutation runs the harness inside a fresh, disposable container:

```bash
docker run --rm --name "mut-${RUN_ID}-${ITER}" \
  --pids-limit 512 --memory 8g --memory-swap 8g --cpus 4 \
  --security-opt no-new-privileges \
  -v "${WORKTREE}:/workspace" -w /workspace \
  ${AUTH_INJECTION} \                  # host's EXISTING harness login, injected automatically (§3.9)
  nethackers/mutator:<digest> \
  timeout 1800 <harness-cmd>            # e.g. claude -p "$BRIEF" --dangerously-skip-permissions
```

- `${WORKTREE}` is the run's existing `work/iter-N/` worktree (per [run-isolation design](2026-08-12-run-isolation-design.md)) — the container's `/workspace` **is** that worktree, bind-mounted; the mutation's edits land there exactly as today.
- **Nothing else is mounted** beyond the workspace and the model-auth path (§3.9) — **never** `~/.ssh`, `~/.aws`, `~/.netrc`, `~/.gitconfig`, or `/var/run/docker.sock`.
- **Hermeticity is structural.** A fresh `--rm` container per iteration has an empty harness home, so the per-cwd/session memory-recall failure the hermetic flags fight (see [run-isolation](2026-08-12-run-isolation-design.md) + hermetic-operator design) cannot occur — provided the only host state that crosses in is the model *credential* (§3.9), never the harness's memory/session/history. Keep the hermetic CLI flags anyway (belt-and-suspenders).
- Cgroup caps (`--pids-limit`/`--memory`/`--cpus`) are the fork-bomb/runaway defense the host operator lacks. The values in the block above are **illustrative starting points, not sourced constants** — size them to the host and to how many mutations run concurrently (per-container caps × concurrency must fit the machine, especially on a 16 GB laptop). The kill-switch tests (§4) exist precisely to prove that whatever values we pick actually bound a fork bomb / runaway, rather than trusting the numbers.

### 3.4 Live-env channel & the mutation brief

Because the whole CLI runs inside the NLE image, **the agent's ordinary Bash/Python tool already has a live NLE** — it runs `python -c "import nle …"` to build an env, roll a candidate policy over training seeds, inspect observations, and iterate, all inside the cage. This is why the sandbox and the live env are the *same* container, and why it is harness-agnostic (every harness exposes a shell/python tool; none needs special integration).

- **Optional convenience (nice-to-have, not required for v1):** pre-bake a tiny `nethackers.mutator_playground` helper module in the image (`make_env(seeds)`, `rollout(agent, seeds)`, `render(obs)`) so the agent doesn't re-derive the harness boilerplate each time. The brief points at it.
- The agent's self-reported numbers ("scores 3400 on 8 seeds") remain a **hypothesis, never the fitness** — authoritative scoring is §3.5.

**The mutation brief.** `build_brief` produces a lean, best-practice-aligned prompt (grounded in `../research/2026-08-10-agentic-evolutionary-search-grounding.md`, `.../guiding-autonomous-search.md`, `.../progress-measurement-structure-analysis.md`). Template:

> **Objective.** Improve this NetHack bot's **progression score** as {character} — the BALROG-style milestone metric the evaluator computes. Maximize *that*; depth/turn-count/survival matter only insofar as they raise it. **Don't game it:** no branching on seed fingerprints (initial glyphs/inventory) to replay a canned run, no exploiting scorer/NLE quirks — such candidates fail on held-out seeds and are rejected.
> **Where it currently loses progression.** Mean {mean} over {episodes} games; outcomes: {tally}.
> **Make one focused change.** One well-reasoned, localized change per candidate; **leave a short comment at the edit stating the hypothesis** (`# hypothesis: …`) so the next iteration inherits your reasoning inline. Wide enough to co-adapt, but a cosmetic/no-op diff wastes an eval.
> **Seeds & the real test.** Develop against your training seeds (provided). Scored on **held-out seeds you'll never see** — generalize, don't memorize; testing on extra random seeds is a good self-check.
> **What you have.** Live Python + NLE; NetHack reference (offline): {wiki_path}; the current bot is your starting point.
> **Before you finalize.** Must import cleanly, keep the `make_agent()` → `reset()`/`act()` contract, and not crash across a handful of seeds — else it scores zero.

Design notes:
- **Objective = the true metric, proxies subordinate** — deletes the shipped `brief.py:18-19` "reach deeper / survive longer" (proxy-chasing; the one best-practice *conflict*, per `progress-measurement-structure-analysis.md:18,149`).
- **Focused, single-hypothesis change — not tiny.** "Bound the surface but wide enough to co-adapt"; trivial/cosmetic diffs are a documented exploit ("elite-laundering via minimal diffs"), not a virtue.
- **Named-exploit guardrail** is paired with "maximize it, and only it" so the objective doesn't invite the #1 reproduced failure (reward-hacking / seed-fingerprint replay).
- **In-code rationale (the user's idea):** the mutator writes its *why* as a comment at edit time — cheap, natural, and the durable vehicle for the distilled-lineage best practice. The *verified outcome* ("helped +X / rejected") is a **loop** step (post-eval), training-tier only, → §7 (a validation delta written into a code comment would re-leak §3.6).
- **New `build_brief` params** required (`wiki_path`, training seeds, later lineage lessons); `tests/test_harness_brief.py` updates with it.

### 3.5 Scorer wall = mount topology

Fitness is never the number the mutation container reports. It is recomputed by the **scorer in a separate container**, and the wall is a filesystem fact, not a policy:

- The loop (host-side) exports the candidate from the worktree at a commit boundary and runs `nethackers/arena` (same NLE base ⇒ deterministic env) with the candidate mounted **read-only** (`-v <candidate>:/sol:ro`), `--network none`, and its own results volume the mutator container never mounts. This is exactly what `eval/runner.py:154` already does — the design **generalizes the existing evaluator isolation**, it does not invent it.
- The mutator container and scorer container share **no writable surface** ⇒ the mutator cannot reach scores, scorer inputs, or the seeds behind them. The residual hacking channel is reward-hacking *within* the candidate code, which is an eval-design problem (multi-seed re-eval, validation gate), not a sandbox problem.

### 3.6 Mutator information diet — training-tier only

The mutator is a second optimizer; anything it can see and push on becomes a training signal. Enforced by mount topology, not trust:

- The mutator container receives **training (dev) seeds + training diagnostics only** (the `brief.py` scorecard: dev mean progression + failure-mode tally). This already holds — `build_brief` receives `dev_evidence` only; `validation_fitness` is a gate value computed in `loop.py:213` and never enters the prompt.
- **Held-out seed *values* stay out; the held-out *objective* goes in.** Validation/held-out seed values and scores never enter the mutator container — they live only in the scorer's context. And because `harness/seeds.py` *derives* them by formula (`validation_spec(start=1000)`, smoke `start=9000`), that generation is **kept out of the mutator image** and the training seeds are handed in as **data** — code-derivable seeds would beat the mount wall. What the brief (§3.4) *does* disclose is that held-out seeds **exist** and that the bot is graded on them (the strongest anti-overfitting instruction) — never *which* seeds. Disclose the objective, hide the values.
- **Parents come from the selector, never the hub.** The mutator has no hub access, no hub creds, and no network path to it: the host-side selector queries the hub, strips candidates to training-tier-safe (code + training diagnostics, *no* validation/held-out scores), and hands the parent — plus, for crossover, K complementary elites — into the container as read-only worktrees / curated brief context. "Give me a *different* solution" is a **selection strategy** (MAP-Elites cell distance / Pareto / novelty) computed host-side — the GigaEvo pattern (selector picks complementary pairs; the mutator only fuses what it is given). This keeps the info-diet wall, the clean cage, and hermeticity intact at once.
- (Richer training diagnostics + distilled lineage credit-assignment are a *feedback* redesign — separate spec, §7 — but they must respect this same training-tier wall.)

### 3.7 `ContainerOperator` — the new backend

A new operator backend at the existing injection seam (`loop.py:177` calls `operator.run(worktree, brief, ...)`; `launch.py:114` selects the class):

- **Same interface, same result contract.** `ContainerOperator.run(worktree, brief, *, on_line, stop)` returns the existing `OperatorResult(backend, usage, stopped_reason)`. It wraps a *chosen harness* (claude/codex/pi/opencode) — so the backend is two-dimensional: **harness × execution-mode (host | container)**.
- **Streaming + metering unchanged.** `docker run` attaches the container's stdout; the harness still emits its `stream-json`/`--json` lines, so `run_operator`'s line loop, `on_line`, and token metering (`harness/metering.py`) work as-is. The container command is just the harness command the host operators already build (`_claude_cmd`/`_codex_cmd`), prefixed with the `docker run …` wrapper and an in-container `timeout`.
- **Stop → `docker kill`.** The shared `stop` event, today an `os.killpg(SIGKILL)` on the process group (`operator.py:62`), becomes `docker kill <name>` (+ the outer `--rm` reaps the container). A wall-clock watchdog outside the container backstops a wedged `docker` client.
- **CLI surface:** a `--sandbox` flag (opt-in for v1) selects `ContainerOperator` over the host operators; `--operator {claude,codex,pi,opencode}` still selects the harness. Default flips to sandboxed once the kill-switch tests (§4) pass in practice.

### 3.8 Host runtime

- **macOS:** one shared **Colima** VM per machine (`colima start --cpu 6 --memory 12 --vm-type vz --mount-type virtiofs`; MIT, free, Apple Virtualization) — or OrbStack if a license is available — hosting many cheap containers. Marginal cost per mutation is a Linux process with cgroup caps, not a VM boot. Explicitly **not** Docker Desktop (heavier; licensed above the employee/revenue threshold).
- **Linux:** native Docker or Podman; no VM.
- Detection: the CLI checks for a working container runtime when `--sandbox` is set and errors early with the Colima/Podman bring-up hint if absent.

### 3.9 Model auth — reuse the host's existing login

**Principle: it just works with the login you already have.** If `claude` / `codex` are authenticated on the host, the sandbox is authenticated — no new accounts, no API keys, no `setup-token` chore, no per-run decisions. The loop injects the host's *existing* credential into each container automatically:

- **Codex** — bind-mount the host's existing `~/.codex` credential (rw). It is the *same* canonical file the host already uses, so OAuth refresh writes straight back and there is a single token lineage (never per-container copies — that is OpenAI's documented CI pattern, and what avoids refresh-token churn).
- **Claude Code** — inject the host's existing credential: on Linux, `~/.claude/.credentials.json`; on macOS the credential lives in the Keychain (unreachable from a Linux container), so nethackers reads it host-side and hands it to the container (a mounted cred file, or `CLAUDE_CODE_OAUTH_TOKEN`), grabbed once and cached. Keep that env var container-only — setting it in the host Mac shell can clobber the Keychain entry (claude-code #37512).
- Only the *credential* crosses the boundary, never the harness's memory/session/history, so §3.3's hermeticity holds. The token living in the container is the accepted accident-grade trade (your machine, your login, your code). **Egress stays open** in v1 (the harness needs the model endpoint + OAuth refresh); the allowlist is deferred hardening (§6), not a gate.

Edge cases are footnotes, not chores: OpenAI's refresh tokens are single-use, so the serial loop sharing the one canonical `~/.codex` file (not copies) is what keeps refresh from colliding — nothing the user manages. Full per-harness detail + sources: [`../research/2026-08-20-account-auth-in-sandbox.md`](../research/2026-08-20-account-auth-in-sandbox.md).

## 4. Testing

**Pure unit (no Docker/NLE):**
- `ContainerOperator` builds the correct `docker run` argv for each harness (image, caps, workspace mount, env, in-container `timeout`, harness command) with a fake `popen`; stop → emits `docker kill <name>`; `OperatorResult` metering matches the host path given identical stream lines.
- CLI: `--sandbox` selects `ContainerOperator`; `--operator` still selects the harness; missing runtime errors early.

**Docker-required (gated marker; runs where a runtime + built image exist):**
- **Kill-switch acceptance criteria** — inside a mutator container: `:(){ :|:& };:` dies at the pids cap with the host untouched; `yes > /dev/null` pegs only its `--cpus`; a big write fills only the volume, not the host; `rm -rf /` destroys only the container; `cat ~/.ssh/id_*` fails (nothing mounted).
- **Scorer isolation** — from a mutator container, attempts to read/write the scorer's results volume and reach the scorer container all fail.
- **Determinism** — the scorer runs the same candidate twice on the same seeds → identical score (parity/repro check across the shared NLE base).
- **Harness-agnostic smoke** — each installed CLI launches inside the image and produces its stream output.
- **Image smoke** — `import nle, gymnasium; make(...).reset()` on both arches (already in image CI).

**Throughput check (not a gate):** measure per-run container startup vs a warm `docker exec` variant; if startup dominates the inner loop, switch to one container per *candidate* (still destroyed between candidates) — recorded as the fallback, not built preemptively.

## 5. Migration / rollout

- Additive and opt-in. The host operators (`ClaudeOperator`/`CodexOperator`) stay; `ContainerOperator` is selected by `--sandbox`. No data migration; the run-isolation layout is unchanged (the container just executes *in* the existing worktree).
- Ships behind the flag; the default execution mode flips to container once the kill-switch suite passes on both a Mac (Colima) and a Linux host.
- The `nethackers/nle-base` split is a refactor of the existing `arena/Dockerfile`; the arena image keeps its current behavior (it just derives from the extracted base).

## 6. Later hardening (deferred, in value order)

Egress allowlist via the `init-firewall.sh` default-DROP + ipset pattern (model APIs + pypi/npm/github); `--read-only` rootfs + `--tmpfs /tmp` with the venv on `/workspace`; `--cap-drop ALL`; image-digest pinning + harness auto-updater disabled. None are needed at accident-grade for v1.

## 7. Sequels (separate specs)

1. **Mutator feedback/lineage** — richer training-tier diagnostics (milestone/where-it-dies) and distilled credit-assignment ("last change gained +X by doing Y"; the hub already records `parent_digest` at `loop.py:226`), plus per-parent behavioral diagnostics when crossover lands. Must respect the §3.6 training-tier wall; curation is load-bearing (Vesper: raw archive/history context *hurt*). One concrete vehicle (user's idea): **in-code rationale** — the mutator already writes its *intent* as a comment at each edit (§3.4 brief); this sequel adds the *verified training-tier outcome* (helped +X / rejected), decided here whether it lives **in-code** (max locality, but risks comment-bloat over generations → needs a cap/prune discipline) or in a **lineage record** the selector distills into the next brief (cleaner, curated). Never the mutator's self-claim; never validation deltas (would re-leak §3.6).
2. **Warm-container / kernel-server** — only if §4's throughput check shows startup dominating.
