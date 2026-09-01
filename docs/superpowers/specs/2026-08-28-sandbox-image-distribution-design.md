# Onboarding, Diagnostics & Sandbox-Image Distribution

**Status:** Design — ready for review
**Date:** 2026-08-28 (restructured 2026-08-29 after two consistency audits + a dev-stack trace; re-anchored to `main` @ **v0.17.0** — the hub API coherence redesign — on 2026-08-29. Parity facts + resolution/config/container-start sites came through the redesign unchanged; only `cli.py`/`eval/runner.py`/`hub/validate.py` line numbers moved.)
**Extends:** `2026-08-16-mutator-sandbox-design.md` (§3.2 image layering — `nle-base` → `arena`/`mutator` siblings, digest-addressed — is the foundation, unchanged).
**Companion:** testing strategy lives in `2026-08-29-onboarding-testing-strategy.md` (see §9).

**How to read this.** Audience is whoever implements it. Read §2 (mental model) and §3 (glossary) first — every later section uses those terms. §4 is the decisions ledger (the *why* behind each choice); §5 is the design; §7 is the invariants the whole thing must preserve (the self-consistency contract). If you change anything, check it against §7.

---

## 1. Problem

Evolve runs **two** Docker images on the user's own machine:

- **`arena`** — the scorer. Used by the CLI's `eval`/`submit`/`smoke` **and** by evolve to score every candidate (`harness/evaluate.py` → `eval_batch`).
- **`mutator`** — the agent sandbox that runs the operator CLI (and that model-discovery probes).

Both are built **only** via `make arena`/`make mutator` from a repo checkout, so an installed (non-repo) user can obtain neither: `evolve` fails at sandbox setup (`can't set up the sandbox: run nethackers from its repo`), and `eval`/`submit`/`smoke` fail too — *worse*, because `arena` has no preflight, no auto-build, no friendly message (a raw mid-run failure).

Two further onboarding gaps: **no readiness check** (nothing answers "is my machine set up?") and **no `nethackers --version`** (a bug report can't say which sandbox bytes / run-format ran).

Root cause under the distribution gap: the images are **under-pinned**. `Dockerfile.mutator:45` installs `@anthropic-ai/claude-code` + `@openai/codex` **unpinned**; `nle-base/Dockerfile:13` uses the floating `FROM python:3.11-slim`; default refs are mutable (`:dev`, `:latest`). So `make` builds a **different image on different days** under one name — the SWE-bench environment-drift failure in miniature.

## 2. Mental model (read first)

**Three surfaces, each with its own needs.**
- **CLI** — needs are **per command**: `eval`/`smoke` need Docker + `arena`; `evolve` needs Docker + `arena` + `mutator` + an operator login; `submit`/publishing need `gh`; `board`/`frontier`/`elites` need only the hub.
- **TUI** — a **full dashboard** (hub browsing, run monitoring, login, **and** evolve). Only the **evolve section** needs the heavy stuff (runtime + `arena` + `mutator` + operator login); the other sections need at most hub reachability + a token. The TUI does **not** run eval/your-own-bot — that's CLI.
- **Hub** — the shared HTTP API the other two speak to.

**Two images + a shared base.** `arena` (scorer) and `mutator` (sandbox) both build `FROM` a shared `nle-base` (the compiled-NLE + pinned-deps layer). `nle-base` is never run directly and never pulled on its own — it's baked into both, and Docker shares its layers.

**The parity model — trust vs trace (this is the one people get wrong).**
- A **score's trust comes only from the verified re-run on the evaluator node** (fixed architecture, holds the seed key the client can't access). That is the anchor.
- The `evaluator_image` digest each atom records is **untrusted traceability** — client-supplied, fakeable, required-present but never gated/ranked/trusted for "same bytes." It's a breadcrumb for debugging, nothing more. **Never build a mechanism that trusts it.**

**Three version regimes — kept separate.**

| Regime | Identity | Changes when | Lives in |
|---|---|---|---|
| Package version | `0.15.0` | every release | `pyproject.toml` |
| **Image content** | a **digest** `@sha256:…` (content-addressed, not a "version") | image inputs change | `_image_pins.py` |
| Run-output format | `RUN_SCHEMA_VERSION="v1"` | a run's publish/record *format* breaks | `harness/version.py` |

Image content is a **digest**, not a version that ticks; `RUN_SCHEMA_VERSION` is **orthogonal** to images (changing one never touches the other).

**Image identity — tag vs digest.** A **tag** (`arena:dev`) is a movable label; a **digest** (`arena@sha256:…`) is an immutable fingerprint of exact bytes. The CLI hard-codes (pins) digests in a source file and, at run time, *resolves* which image to use via a ladder (§5.1).

## 3. Glossary

- **arena / mutator / nle-base** — the scorer image / the agent-sandbox image / their shared compiled-NLE base. See §2.
- **operator** — the coding-agent CLI a run wraps (`codex` or `claude`). *The single name for this concept* (the code's `operator` param; the docstring's "harness"; "agent CLI"). Always "operator" in this doc.
- **content / platform / manifest-list digest** — a *content* digest names exact bytes; a **platform digest** names the bytes for one arch (amd64 *or* arm64); a **manifest-list digest** names a list pointing at both. These are different — §4 D4.
- **pin** — a digest hard-coded in `src/nethackers/_image_pins.py`, shipped in the wheel.
- **resolve** — pick the actual image ref for this run via the ladder (§5.1).
- **warm** — pull an image ahead of first use (`doctor --pull`).
- **atom / Evidence** — the scored record submitted to the hub; carries `evaluator_image` (the untrusted trace) and no operator/model field.
- **capability** — an intent doctor reports on: `eval`, `evolve`, `publish`, `browse`.
- **the seams** — the injectable dependencies the code already exposes (`run=`, `which=`, `image_digest_resolver=`, `http=`, `home=`, `_now`); used for testing (companion doc).

## 4. Decisions ledger

Each: **decision — why (one line) — rejected alternative — consequence.**

- **D1. Images are content-addressed (digest), not keyed to a version.** The scored environment is the `arena` image; the operator is outside it, and the recorded digest is untrusted trace anyway — a hand-bumped version regime adds nothing and misleads. *Rejected:* keying images to `HARNESS_VERSION`/`RUN_SCHEMA_VERSION` (a category error). *Consequence:* `_image_pins.py` + a re-pin ceremony (D11).
- **D2. Publish both `arena` and `mutator`.** `arena` is the scorer — used by CLI `eval`/`submit` *and* evolve scoring — so the bug is bigger than the mutator-only error implies. *Rejected:* fix only `mutator`. *Consequence:* both get a preflight + pull path; `arena` gains guardrails it lacks entirely today.
- **D3. Public GHCR.** The image is a subset of the already-public PyPI wheel; an external one-line install needs anonymous pull; nothing new is exposed. *Rejected:* private (breaks one-line install). *Consequence:* none.
- **D4. Multi-arch, but parity stays single-arch.** arm64 Macs want fast local iteration; official scoring re-runs on the evaluator (fixed arch) and the recorded digest is untrusted trace, so cross-arch local divergence is harmless. *Rejected:* single-arch-only (slow Mac local runs); *or* trusting a manifest-list digest as "same bytes" (it addresses two byte-sets — false). *Consequence:* record the resolved **platform** digest as the breadcrumb; local scores are **advisory** (arm64 Macs already diverge today); never gate on the digest.
- **D5. Pin the operator CLIs, but rev them on the image's own content line — not coupled to any version regime.** The operator is outside the scored boundary; freezing it to a "contract" is "bump the world to update codex." *Rejected:* freeze the CLIs under `RUN_SCHEMA_VERSION`. *Consequence:* updating a CLI is a content change → new `mutator` digest → re-pin.
- **D6. Sentinel config default + a `resolve_image()` ladder.** A config field can't be *both* the pin *and* let a repo build win, and can't tell an explicit override from a defaulted pin. *Rejected:* default the field to the pin (breaks repo dev; conflates the cases). *Consequence:* the field defaults to `None`; `resolve_image` applies the ladder (§5.1); ~10 readers must route through it; **"explicit" = the layered field is non-None** (so `.env.stack` counts); never build a digest ref; no build side-effects inside `resolve_image`.
- **D7. `doctor` is per-capability, read-only + one `--pull`.** Requirements differ by intent; an eval-only user shouldn't be "not ready" for lacking `mutator`/operator. *Rejected:* one global "hard" set (wrongly fails eval-only users). *Consequence:* `--for <capability>` sets the exit code; JSON exposes per-capability booleans; the exit fold is a pure function (the test oracle).
- **D8. `--version` = the three regimes, offline, pins-not-state.** docker/kubectl multi-axis precedent; a bug report needs the sandbox identity; offline because it reports *pins*, not what's pulled (that's `doctor`). *Rejected:* a terse package-only string. *Consequence:* prints package + `run-schema` + the pinned digests; honors `-o`.
- **D9. The TUI never blocks launch; heavy requirements are scoped to the evolve section.** Browse/monitor/login need nothing heavy; gating launch on Docker is the k9s/lazydocker anti-pattern. *Rejected:* a global readiness gate / a global home strip. *Consequence:* readiness + pull provisioning live *in the evolve section*; resolution must reach the mount-time operator probe, or bare `nethackers` crashes (Appendix B).
- **D10. Lazy pull + `doctor --pull`; no eager pull, no `setup` verb.** Opening the TUI or running `--version` must not download GBs; Ollama's run-auto-pulls / pull-pre-warms split. *Rejected:* eager-at-launch; a separate `setup` verb. *Consequence:* the pull is consented at the point of intent (evolve Start / first `eval`); size disclosed; disk checked against **extracted** size.
- **D11. Re-pin ceremony: build+push first, commit the digest, then tag/release.** The digest exists only after build+push (chicken-and-egg). *Rejected:* resolve the digest at release time inside the wheel (non-reproducible). *Consequence:* a `workflow_dispatch` image build + a committed `_image_pins.py`; most releases don't touch it. (`kind`'s pattern.)
- **D12. A wheel can't ship unless its pinned digests exist in GHCR, + a staleness tripwire.** A pip user must never land in the can't-get-sandbox hole; "changed the image, forgot to re-pin" must fail loudly. *Rejected:* no gate (the original bug). *Consequence:* a cross-workflow poll gate in `publish-pypi.yml`; a tripwire diffing image-input paths against the last released tag.
- **D13. Field detection = zero telemetry; a crash file + `nethackers report`.** The reference class (uv/ruff/pip) ships none; client data is untrusted anyway; the missing thing is *crash evidence*, which `main()` currently discards. *Rejected:* opt-out telemetry (documented backlash). *Consequence:* `main()` writes a local crash file; `nethackers report` packages `doctor -o json`; a server-side `User-Agent` funnel (the hub is ours); an `rc` beta ring — after guarding `hub-image.yml`'s deploy against pre-release tags (Appendix B).
- **D14. Every container we start is named `nethackers-<role>-<unique>` (+ a `nethackers` label).** Random Docker names make manual `docker ps` / cleanup / inspection painful. *Rejected:* rely on `--rm` + the compose project name only — that leaves the ad-hoc `docker run`s (arena scoring, operator probes) unnamed. *Consequence:* a shared `container_name(role)` helper; every `docker run` passes `--name` + `--label`; the mutator's existing `mut-…` name gains the prefix; compose is already `nethackers-`-prefixed via its project (§5.13).

## 5. Components

### 5.1 Image resolution — the ladder (everything below depends on this)
The config fields `arena_image`/`mutator_image` default to a **sentinel `None`** ("nothing set"). A `resolve_image(kind, explicit)` function — where `explicit` is the *layered* `Stage` value (flag < `.env.stack` file < env; `None` iff none of those set it) — returns the ref:
1. **`explicit is not None`** → use it verbatim (never auto-build over it). *This includes `.env.stack`* — a worktree's `NETHACKERS_ARENA_IMAGE=nethackers/arena:<slug>` (written by `scripts/stack.py`) is what `make` built there, so it must win.
2. **else `_repo_root()` found** (a checkout with `Dockerfile.mutator`+`Makefile`) → the local `make`-built ref (`nethackers/arena:dev` / `nethackers/mutator:latest`). Dev path.
3. **else** → the pinned `ghcr.io/…@sha256:…` from `_image_pins.py`. Installed-user path.

Rules: **never build a digest ref** (`docker build -t …@sha256` is invalid — step 3 pulls, never builds); **no build side-effects inside `resolve_image`** (it's called from `EvolveParams` default factories — provisioning stays at the explicit preflight points); every reader of the two config fields routes through this (else `None` reaches docker as a `TypeError` — see Appendix B for the ~10 sites, incl. the TUI mount-time probe that otherwise crashes launch).

### 5.2 Pin the image contents
- `nle-base/Dockerfile`: digest-pin the base — `FROM python:3.11-slim@sha256:…`. (Deps + `PYTHONHASHSEED=0`/single-thread BLAS already pinned.)
- `Dockerfile.mutator`: pin the operator CLIs to exact versions (`npm i -g @anthropic-ai/claude-code@<X> @openai/codex@<Y>`) and pin the Node setup.
- apt left unpinned for v1 (build/runtime tooling, non-scoring; §10). Result: given the repo at a commit, the images are reproducible modulo apt.

### 5.3 Distribution — GHCR, public, multi-arch
- Publish `ghcr.io/dunnolab/nethackers-arena` + `…-mutator` (mirrors `nethackers-hub`), **public** (anonymous pull). Safe per D3.
- **Multi-arch manifest** (`amd64` + `arm64`). Rebuilds are rare (only on input change), so the arm64 half under QEMU is acceptable. **CI builds `nle-base` once and both images build `FROM` that exact digest** — this is the precondition that makes "pull both, fetch the base once" true.
- Every push carries a **permanent unique tag** (`sha-<gitsha>`) alongside the digest pin: GHCR has no immutable tags and its GC deletes untagged digests (which would `manifest unknown` a released wheel), so the tag keeps the pinned digest alive.

### 5.4 Digest pins in source + re-pin ceremony
`src/nethackers/_image_pins.py` (generated) is the **single source** of the pinned refs:
```python
ARENA_IMAGE   = "ghcr.io/dunnolab/nethackers-arena@sha256:…"
MUTATOR_IMAGE = "ghcr.io/dunnolab/nethackers-mutator@sha256:…"
```
Everything that needs a pinned ref reads it from here (do not re-render the shape elsewhere). Ceremony (D11): change an image input → run the image workflow (builds+pushes multi-arch, prints digests) → commit them into `_image_pins.py` → bump `pyproject` + merge → tag → release. Most releases skip this and the pins carry forward.

### 5.5 Acquisition — preflight, progress, errors
**Two separate gates** (do *not* fuse them — `arena` has no operator):
- **Runtime + image** (both images): a working container runtime, then `resolve_image` (§5.1); if the resolved ref is a pin and absent locally, `docker pull` it.
- **Operator-auth gate** (evolve/`mutator` only): a resolvable `codex`/`claude` login. A plain `eval` must **not** demand an operator login. (Today's `sandbox_preflight.preflight(operator, …)` bundles both — split it.)

**Progress** is a shared mechanism (used by the CLI and the TUI): the pull path emits **typed progress events** (aggregated bytes/percent or "layers m/n"), never raw `docker pull` per-layer output. Concurrent pulls of the same ref **dedup** (one pull, others wait) rather than racing.

**Every failure message ends in exactly one runnable command** (INV9): 404 → "no published sandbox for this build; clone the repo or set `NETHACKERS_*_IMAGE`"; 403 → stale ghcr login → "run `docker logout ghcr.io`"; offline → "only the first run needs the network."

### 5.6 `doctor` — per-capability readiness
Answers "is my machine set up to do X?" for each **capability**, read-only (only `--pull` mutates). Beautiful on a TTY, `-o json` for scripts/bug-reports, never a traceback.

Per-capability blocks, each ✓/⚠/✗ with one fix:
- **To eval / run a bot** (`eval`, `submit`-scoring): Docker + `arena`.
- **To evolve:** Docker + `arena` + `mutator` + an operator login.
- **To publish wins:** two *distinct* GitHub auths, each its own row + fix, because people constantly conflate them — **hub login** (`nethackers login`: the device-flow token that lets the *hub* accept your scores) and **GitHub CLI auth** (`gh auth login`: lets `gh` push your winning *solution* to your public repo so the hub's commit-exists check can see it). The `gh` check reports **three** states, not two — *not installed* (→ install `gh`), *installed but not authed* (→ `gh auth login`), *authed* (✓) — never collapsing "installed" and "authed" into one message.
- **To browse the hub:** hub reachable (+ optional login).

Image checks report `present` / `pullable` (cheap `docker manifest inspect`) / `unreachable`, with the pinned digest, its **extracted** size, and **free disk**. Hub/login reuse `whoami`'s `hub_mode()` + `_effective_identity`. Operator resolves host `codex`/`claude` creds (`--operator` defaults to evolve's default operator; bare `doctor` reports the configured default and notes the other).

**Contract** (the exit-code genre's failures are all this fold left implicit):
- exit code is a **pure function** of the check results *for the requested capability*: `doctor --for eval|evolve|publish` → 0 iff that capability's checks pass; bare `doctor` → 0 iff **eval-ready** (the minimum useful), and prints every capability.
- `-o json` obeys the same code and emits `{checks: [{id, status, severity, detail, fix, capability}], capabilities: {eval, evolve, publish, browse: bool}, env: {nethackers, run_schema_version, images:{arena,mutator}, os, arch, python}}`. This `env` header is the single builder; `--version` (§5.7) is that header minus the machine fields. `doctor -o json` is a complete bug-report attachment.

**Surfaced at the moment of intent, not only here.** `doctor` is opt-in, and the two GitHub auths are the single most-forgotten setup step — a hub-logged-in but `gh`-unauthed contestant currently evolves, wins, and the win **silently stays local** (`PublishError` at push time → kept as a local elite; a documented cause of "wins don't register"). So the CLI `evolve` and the TUI evolve panel **pre-check publish-readiness at Start** and warn on *either* missing auth — extending today's hub-only warning (`cli.py:676`) to cover `gh` too, reusing the `gh_login` probe `submit` already runs (`cli.py:813`, whose "install … and run `gh auth login`" message is itself the installed-vs-authed conflation to fix). A missing `gh auth` must fail **loudly and up front**, never silently and late.

### 5.7 `--version`
A top-level flag, handled before dispatch, honoring `-o`. **Offline; reports the pins, not pulled state** (state is `doctor`'s job; what actually ran is the per-run record, §5.9).
- **human / `table` / `plain`:** `nethackers 0.15.0` / `run-schema v1` / `arena …@sha256:abc1234…` / `mutator …@sha256:def5678…` (short digests).
- **`-o json`:** the `env` header minus `os/arch/python` (full refs). Sources: package via `importlib.metadata`, refs via `_image_pins.py`, `run-schema` via `harness/version.py`.

### 5.8 TUI — dashboard, with the evolve section carrying the requirements
Bare `nethackers` opens the dashboard and **must open regardless of readiness** — browse/monitor/login need no Docker/images/operator (k9s/lazydocker crashes are the anti-pattern). Only the **evolve section** needs the heavy stuff, so:
- **Readiness + pull provisioning live in the evolve section** — on entering evolve you see "here's what evolve needs" (runtime · `arena`+`mutator` · operator), read from the **same functions `doctor`'s `evolve` capability uses** (INV5), using the cheap **local** `image inspect` (no GHCR round-trip on navigation).
- The pull is consented at **Start** (size disclosed); model-discovery **degrades** (shows "pull the sandbox to see models") when `mutator` is absent — it never forces a pull.
- The **pull-progress surface** uses the shared typed-event stream (§5.5): image + short digest, "~N GB, one-time," aggregate progress, cancel, auto-continue.

### 5.9 Provenance per run
`runlog.write_run_config` (`launch.py`, evolve) records the resolved `arena`+`mutator` **platform** digests (D4), the in-container `codex`/`claude --version`, and the model id (from the resolved run params). This is the evolve run's traceability record. (Distinct from `Evidence.evaluator_image` on an *atom*, which is the untrusted per-score trace — INV1/INV7.) Honest reproducibility boundary: the run is *identified*, though the model API behind the operator floats.

### 5.10 Crash file + `nethackers report`
`main()`'s guard currently discards the traceback of exactly the failures field-testing misses. Instead it writes `~/.nethackers/crash/<ts>.json` (trimmed to `nethackers` frames, redacted argv, versions, `doctor -o json`) and prints "saved locally — run `nethackers report`; nothing is sent until you do." `nethackers report` shows the payload and opens a prefilled issue (clipboard for the doctor blob). Zero background telemetry (D13).

### 5.11 Hub client `User-Agent`
Stamp `nethackers/<ver> (<py>; <os-arch>) cmd/<name>` on hub calls so the hub can measure version adoption + the activation funnel server-side (the calls users already make). Disclosed in one README line.

### 5.12 CI / release wiring
- New `sandbox-images.yml` (mirrors `hub-image.yml`): `workflow_dispatch`, builds+pushes `arena`+`mutator` multi-arch with `sha-<gitsha>` tags, emits digests; a step (or bot PR) writes them into `_image_pins.py`.
- `publish-pypi.yml` gains one gate before `uv publish`: poll GHCR for the pinned digests, fail if absent (D12).
- **Staleness tripwire:** fail CI if image-input paths (`Dockerfile.mutator`, `nle-base/Dockerfile`, `arena/Dockerfile`, `src/nethackers/arena/`, `src/nethackers/contracts/`, `uv.lock`) changed **since the last released tag** without `_image_pins.py` changing.

### 5.13 Container naming
Every container we start carries a `nethackers-<role>-<unique>` name and a `nethackers` label, so `docker ps -f name=nethackers-` (or `-f label=nethackers`) lists them and `docker rm $(docker ps -aq -f label=nethackers)` cleans them up. One `container_name(role)` helper is the source of the convention. Sites:
- `eval/runner.py:165` (arena scoring) — today **unnamed/random** → `nethackers-arena-<hex>`.
- `harness/discovery.py:57,83` (operator probes) — today **unnamed/random** → `nethackers-probe-<hex>`.
- `harness/container_operator.py:84` (mutator sandbox) — today `mut-<run_id>-<iter>` → `nethackers-mut-<run_id>-<iter>` (keeps the inspectable run/iter suffix; amends the `2026-08-16` spec §3.3 name; `_maybe_kill_on_stop` uses the same string, so the stop path stays consistent).
- Compose (`make up`) — already `nethackers-<service>-<n>` via the `nethackers` compose project; unchanged.

The `<unique>` suffix is required because a `--name` must be unique among running containers, and evals/probes run concurrently under `--max-parallel-evals`.

## 6. Onboarding path

| Step | Action | Acquires |
|---|---|---|
| 0. Prereqs | container runtime (Docker/Colima); optionally `gh` | — (hard: runtime; soft: `gh`) |
| 1. Install | **`uv tool install nethackers`** · `pipx …` · `pip …` (own venv) | CLI + the pins (`_image_pins.py`) — **no images** |
| 2. Check | `nethackers doctor` (`--pull` warms) | reports per-capability readiness |
| 3. Log in | `nethackers login` (device flow); optionally `gh auth login` | hub token |
| 4. Evolve | `nethackers` → evolve section → Start | pulls `mutator`+`arena` on first use |
| 5. Eval | `nethackers eval …`/`submit` | pulls `arena` on first use |

`pip`/`uv` never fetch images at install time (they can't run Docker). Disclose pull size before pulling; check **extracted** size against free disk (the two images share the `nle-base` layer, so their combined footprint is less than the sum — report the real extracted total, not compressed×2).

## 7. Invariants (the self-consistency contract — check changes against these)

1. **Trust = the verified re-run on the evaluator (fixed arch).** The recorded `evaluator_image` digest is untrusted traceability — never gated, ranked, or trusted for "same bytes."
2. **Every image ref goes through `resolve_image()`** — the raw config field is never a docker ref. Repo → local build; non-repo → the pin.
3. **"Explicit" = the layered `Stage` field is non-None** — flag, env, *or* `.env.stack` (so a worktree's per-slug image wins).
4. **`RUN_SCHEMA_VERSION` is orthogonal to images** — changing it never (re)builds an image; an image change never touches it.
5. **One source of truth for readiness** — `doctor`'s per-capability checks are the *same functions* the TUI evolve panel reads.
6. **`doctor` exit code = a pure function of the requested capability's checks;** soft warnings never flip it.
7. **Three questions, three surfaces:** `--version` = the pins (offline); `doctor` = actual local/registry state; the per-run record = what actually ran (the platform digest).
8. **The TUI never blocks launch;** a not-ready state is a calm inline state in the evolve section, never a crash — including the mount-time operator probe.
9. **Every user-facing failure ends in exactly one runnable command.**
10. **A wheel reaches PyPI only if its pinned digests exist in GHCR.**
11. **Never build a digest ref;** resolve step 3 pulls, never builds.
12. **`scenarios.yaml` (companion doc) is the only hand-authored test matrix;** tests parametrize from it.
13. **Every container we start is `docker ps`-filterable by `name=nethackers-` (and `label=nethackers`)** — no random-named nethackers containers.
14. **Publish-readiness — the hub login *and* `gh auth` — is surfaced at the moment of intent** (evolve Start / the TUI evolve panel), each named distinctly, never only in opt-in `doctor`; a missing `gh auth` never fails silently-and-late (win kept local). The `gh` check distinguishes not-installed from installed-but-unauthed.

## 8. Rollout

- **Bootstrap:** run `sandbox-images.yml` once → publish `arena`+`mutator` → commit initial pins → cut a release. Pins + gate + `doctor`/`--version` go live.
- **Migration for the sentinel (D6):** route the ~10 readers (Appendix B) through `resolve_image`; the dev stack (`make`/compose/`.env.stack`) is otherwise untouched.
- **Docs:** flip `README.md` (currently `pip`-first) to lead with `uv tool install nethackers`, state one-line prereqs, point at `nethackers doctor`.
- **Phase 0 (shippable now, independent of the rest):** the `[tool.uv] cache-keys` fix (Appendix B); the exact-version install smoke (CI + post-publish canary); wire the existing compose walking-skeleton into CI as one required docker job; the crash-file + `report` path.
- **Backward-compat:** `RUN_SCHEMA_VERSION` and the `evo-harness-v1/` namespace untouched; `nethackers/arena:dev`/`nethackers/mutator:latest` stay valid inside a checkout; `whoami` unchanged; `doctor` is additive.

## 9. Testing

The strategy — the `scenarios.yaml` matrix, `doctor -o json` as the oracle, the `NETHACKERS_FAULT` switch, the two test tiers, snapshot/Pilot, contract testing, and field-detection testing — is its own document: **`2026-08-29-onboarding-testing-strategy.md`**. It's referenced by INV12 and by the Phase-0 list above (the install smoke + the walking-skeleton wiring).

## 10. Open decisions (genuinely unresolved)

1. **apt pinning** — leave unpinned for v1 (non-scoring) **[recommended]** vs pin fully.
2. **Re-pin mechanism** — `workflow_dispatch` + manual commit of `_image_pins.py` **[recommended for v1]** vs a Renovate-style bot PR.
3. **Substrate-match check** — assert the `mutator`'s baked `/opt/kit` == `arena`'s at build time: **hard-fail [recommended]** vs warn (failure is soft — mis-calibrates in-loop feedback, can't corrupt official scores).
4. **`doctor` bare-exit default** — exit 0 = eval-ready **[recommended]** vs evolve-ready.

(Everything under §4 is *decided*, not open. The behavioral "golden-score" comparability gate stays out of scope — it would be a *separate* mechanism, never `RUN_SCHEMA_VERSION`, and per INV1 it must not trust a client digest.)

## Appendix A — Verified codebase facts

- `eval/runner.py:197` records `evaluator_image = image_digest_resolver(image)`; `_default_image_digest` (`runner.py:93`) returns `RepoDigests[0]` else `.Id` (the local build's `.Id` is already platform-specific — D4's "record the platform digest" is preserving today's honesty, not new machinery).
- `hub/validate.py:176` raises `MissingImage` on empty `evaluator_image` — the *only* check; nothing gates/ranks on its value (INV1 confirmed, and it survived the v0.17.0 register-write-model rewrite unchanged).
- `Evidence`/`Atom` (`contracts/models.py`) carry `evaluator_image` and **no operator/model field**.
- `nle-base/Dockerfile:13` floating `FROM python:3.11-slim`; `Dockerfile.mutator:45` unpinned `npm i -g`; `config.py` defaults `nethackers/arena:dev` / `nethackers/mutator:latest`.
- `scripts/stack.py:76,81` writes `NETHACKERS_ARENA_IMAGE=nethackers/arena:<slug>` + `…MUTATOR_IMAGE=…:latest` into each worktree's `.env.stack` (INV3's reason).

## Appendix B — Pre-existing defects this work surfaced (fix candidates)

*(Re-verified against `main` @ v0.17.0 — all still present; the coherence redesign centralized `solution→program` + facet vocab but did not touch these. Its **19** still-untyped `hub/api.py` handlers and persisting `"unknown objective"` ×3 make the companion doc's contract-testing + one-vocabulary lint more urgent, not less.)*

- **uv stale-cache "0.0.0"** (reproduced): fix = add `[tool.uv] cache-keys = [{file="pyproject.toml"},{file="src/**/*.py"}]`.
- **The sentinel (D6) breaks ~10 readers unless routed** — sharpest: the TUI **evolve form probes `mutator` at mount** (`evolve_form.py:254`), so `None` → `image_present(None)` → `TypeError` → **bare `nethackers` crashes at launch**; and the autouse `_hermetic_tui_discovery` stub (`conftest.py:8`) masks exactly this, so the suite stays green while launch is broken. Other sites: `cli.py:346/358/390/403/502`, `launch.py:68/78`, `models`/`preflight_model` silently flip to probing the host CLI (`image=None` already means that in `discovery.py`).
- **Vocabulary drift:** `"unknown objective"` ×3 spellings; the 73-identity universe mirrored in Python `IDENTITIES`, web `ROLE_VARS`, and `wire.test.mjs`; `wire.test.mjs` not in CI.
- **FastAPI handlers return `dict[str, Any]`** → empty OpenAPI response schema.
- **credentials under `Path.home()`, not `NETHACKERS_DATA_ROOT`** (isolation gotcha).
- **`test_cli_m2a.py:169` pokes `console._width = 200`** (a terminal-pinning fixture supersedes it — companion doc).
- **`textual>=0.60` floor** likely untestably old (`--resolution lowest-direct` would prove it).
- **`hub-image.yml` triggers on any `v*`** → tagging `v0.16.0rc1` for a beta ring would CD-deploy prod; guard the deploy job against pre-release tags before D13's ring.
