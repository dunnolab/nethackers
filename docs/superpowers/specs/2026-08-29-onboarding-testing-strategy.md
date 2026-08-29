# Onboarding Testing Strategy

**Status:** Design — ready for review
**Date:** 2026-08-29 (endpoint/vocab examples aligned to `main` @ **v0.17.0** — the hub API coherence redesign)
**Companion to:** `2026-08-28-sandbox-image-distribution-design.md` (the design being tested). Referenced there by INV12 and the Phase-0 list. Validated by a 10-way best-practices pass that converged on one architecture.

**Goal.** Catch onboarding / first-run UX problems *before* users hit them, across the surfaces (CLI, TUI, hub) and the messy environments real users have (clean machine, a prior/parallel install, Docker down, offline, arm64/amd64, stale images). Terms (`operator`, `arena`/`mutator`, `resolve_image`, the seams, capability) are defined in the design's §3.

---

## 1. The problem shape: journeys × environmental states

Two orthogonal axes; the scenarios we want to track are the grid.

- **Journeys** (persona critical paths): **contestant** (install → `doctor` → author a bot → `eval` → `submit` → board-shows-you — *load-bearing*); spectator (browse the hub); operator (hub/worker/images); researcher (Python API + arena).
- **Environmental state — 5–6 orthogonal *factors*, not one axis** (they co-occur): **install** (clean / prior-or-parallel install / stale cache), **runtime** (up / down / missing / Colima-stopped), **auth** (logged-out / expired / `gh`-unauthed), **network** (online / offline / 404 / stale-login 403), **arch** (amd64 / arm64), **image-state** (absent / present / stale).

The full cross is ~48k cells; §6 reduces it to ~50–60 by pairwise generation.

## 2. The mechanism (one architecture)

**Source of truth — `tests/scenarios/scenarios.yaml`, which pytest parametrizes from** (so the matrix *executes* — it can't drift from the tests, the lesson from Kubernetes abandoning its hand-curated conformance YAML). A conftest loader turns each `status: automated` row into a `pytest.param`; `manual`/`todo` rows collect as skip/xfail, so `pyproject`'s existing `-ra` prints the uncovered-cell report for free.

**Oracle — `doctor -o json`** (the design's §5.6 gives it a stable contract precisely so it can be this). Each matrix cell is a **three-beat**:
1. assert the **exit code** (per the requested capability),
2. assert the JSON is **schema-valid** (a committed `doctor.schema.json` with `id`/`status`/`severity` as enums + a drift gate),
3. **pinpoint the state** on `id`/`status`/`severity` *only* — never `detail` copy (that's the churning human surface).

Beat 2 doubles as **fixture verification** — it proves the *seeded fault actually took*, killing the false-green where the surface test passes for the wrong reason. The exit-code fold is a **pure function** pinned by an exhaustive status×severity table test (npm/expo/gh exit-0-on-error and brew exit-1-on-warning are all that fold left implicit).

**One fault mechanism — `NETHACKERS_FAULT=docker-down,registry-403,…`**, a failpoint switch wired to the seams the code already exposes (`run=`, `which=`, `image_digest_resolver=`, `http=`, `_now`). It serves unit tests, subprocess E2E, *and* human review (`NETHACKERS_FAULT=docker-down nethackers` renders every degraded screen with no Colima to stop, no token to burn, nothing pulled). Distinguish the three runtime states precisely: **missing** (`which` → None), **down** (`docker info` rc≠0), **wedged/slow** (`TimeoutExpired`).

**Fixtures — (HOME tarball + a dind `/var/lib/docker` volume)** on one clean base image. A dirty-machine cell becomes a two-line fixture: `run(scenario_image, home_fixture, dockerd_fixture)`. `home_fixture` captures login + caches + data (note: credentials live under `Path.home()`, *not* `NETHACKERS_DATA_ROOT`, so only `HOME` isolates them); `dockerd_fixture` is a warmed/empty/stale image store. `testcontainers-python` + a `docker:dind` sidecar supplies the runtime the tool itself needs; GitHub Actions `ubuntu` + `ubuntu-arm` fresh runners give clean machines + both arches.

## 3. Two tiers + a thin third

- **Tier 1 — every commit, ~90%: inject at the seams, snapshot the output.** No real daemon, no pull. This is where the whole state matrix lives cheaply and deterministically.
- **Tier 2 — nightly/release, few, real: ephemeral containers.** The **one contestant walking-skeleton** E2E runs here (install the built artifact → `doctor` → author a template bot → `eval` → `submit` → board-shows-you) and emits **time-to-first-success** as a tracked number; a live nightly twin against the real hub doubles as synthetic monitoring. Keep this tier small (Google's "just say no to *more* E2E tests" — the failure mode is *many*, not *one*).
- **Tier 3 — human/motion:** `vhs` gifs of the pull/onboarding; **one real-PTY `pexpect` smoke** (the only thing that exercises the `isatty()` gate — see §8); one real ~3 GB pull per release; a quarterly "stranger run" (someone off-team, clean machine, README only, stopwatch).

## 4. Per-surface tooling

- **CLI text — `syrupy` snapshots behind an autouse terminal-pinning fixture** (`COLUMNS/LINES/NO_COLOR/TTY_COMPATIBLE`; delete `FORCE_COLOR`/`NETHACKERS_*`) + a `scrub()` normalizer for digests/paths/durations, with a companion plain-assert so a scrubbed snapshot can't pass on garbage. One parametrized test per surface yields the per-state failure-copy matrix in one file. **A corpus lint enforces INV9** ("every error's last line is a backticked runnable command").
- **TUI — Textual `Pilot` state-assertions (~90%)** + a *capped* `pytest-textual-snapshot` gallery of canonical states (lazygit/harlequin both fled large snapshot suites). No wall-clock in any rendered string; `TEXTUAL_ANIMATIONS=none`.
- **The typed progress-event seam (design §5.5).** The pull path emits `PullEvent`s from an injected stream, replayed from **one recorded real-pull JSONL**. This makes the in-TUI pull testable, feeds the CLI progress fixture, and doubles as the `vhs` demo — one seam, three payoffs.
- **Install/packaging matrix.** Build the wheel once; every job installs the *artifact* (never the source tree). The load-bearing assertion: install per method (`uv tool`/`pipx`/`pip`), run `nethackers --version` **from an empty dir**, string-compare to the built version — catches stale wheel / shadowing binary / refused upgrade / empty wheel. Reuse it as the **post-publish canary** (`uvx nethackers@latest --version`). Matrix: python-spread on ubuntu + `include:` for macos/windows on one python. `--resolution lowest-direct` to prove declared floors aren't lies.
- **Cross-surface + API contract.** The **v0.17.0 coherence redesign just shipped** a large `solution→program` rename + `/programs`/`prog_`-id/envelope sweep with its handlers *still* returning `dict[str, Any]` (**19** of them) — so this lane is concretely urgent, not hypothetical (it's exactly the change contract tests exist to guard). Give the hub **typed response models** (Pydantic, housed in `contracts/`) → commit `openapi.json` → gate breaking changes with **oasdiff** → run **schemathesis** in-process against `create_app` + `load_fixtures` over the current enveloped routes (`/programs`, `/programs/{id}`, `/programs/{id}/identities`, `/elites`, `/board?scope=`, `/register`→`program_id`, `/atoms`, `/hackers/leaders`, `/achievements/{milestones,coverage,firsts}`) → replace every surface's hand-rolled canned JSON with **provider-generated golden fixtures** shared by the Python tests *and* `wire.test.mjs` (and put `wire.test.mjs` in CI). Plus a **one-vocabulary lint**: the redesign centralized the *Python* facet vocab (`hub/objectives.py` `IDENTITIES`/`FACETS`), but the web `ROLE_VARS` (`index.html`) + `wire.test.mjs` `ROLE_VARS` still mirror it and `"unknown objective"` still has ×3 spellings — a grep test failing on those literals outside one catalog closes the gap.

## 5. Field detection — zero telemetry

(Design D13 / §5.10–5.11.) Reference class is uv/ruff/pip — none ship client telemetry, and per INV1 nothing the client reports is trusted anyway. So: the **crash file + `nethackers report`** (turns the traceback `main()` currently *destroys* into a consented, `doctor`-enriched payload); an **issue template that requires `doctor -o json`**; a **server-side funnel** from the hub `User-Agent`; a **PyPI `rc` beta ring** (after guarding `hub-image.yml`'s deploy against pre-release tags). Do **not** instrument file paths, program contents, error strings, hostnames, or any durable "anonymous" id.

## 6. The scenario matrix as an artifact

- **Pairwise generation (Microsoft PICT)** — a small checked-in `model.pict` with constraints, weights (bias to contestant/CLI), and sub-models (3-way for the hot `auth×surface`, `network×surface`; 2-way elsewhere). NIST's data: ≤2-way factor interactions cause the large majority of defects, so ~50–60 rows cover most of the ~48k cross. Regenerate at design time (with PICT seeding to keep row ids stable), never in CI.
- **Row shape:** `{id (slug, never reused), journey, surface, install, auth, network, arch, images, tier, expect, origin: pairwise|pinned|incident, status: automated|manual|todo}`. `expect` follows the web-platform-tests default-is-good-UX model; `origin` is the anti-rot antibody (a row must say *why* it exists).
- **Tiers, Rust-target-style:** Tier 1 blocks PRs; Tier 2 informs (nightly); Tier 3 declared-but-manual. Promotion is a one-field PR.
- **Coverage visible:** a rendered grid (journey × factor, colored automated/manual/todo/n-a) + a verify step (Kubernetes conformance-golden-file pattern) asserting every automated row has a test and vice-versa. A feature's Definition of Done includes "which rows does this add/change?"

## 7. Phases

- **Phase 0 (shippable now — half are bug fixes):** the `[tool.uv] cache-keys` fix; the exact-version install smoke (CI + post-publish canary); wire the **already-built** compose walking-skeleton (`test_compose_smoke.py` + `LocalStubAuth`/`NETHACKERS_STUB_IDENTITIES` + `tests/fixtures/bots/valid_bot`) into CI as one required docker job — today `ci.yml`'s `-m "not docker"` lets install→submit→board breaks pass green; the autouse terminal-pinning fixture; the crash-file + `report` path.
- **Phase 1 (spine):** `doctor` as three pure stages + `doctor.schema.json` + the exit-code table test; the `NETHACKERS_FAULT` switch; `scenarios.yaml` + the doctor-oracle three-beat; the typed progress-event seam.
- **Phase 2 (breadth):** the CLI snapshot corpus + error-copy lint; TUI Pilot + capped snapshots + the PTY smoke; the ephemeral-container substrate + install matrix; contract testing (typed responses + openapi + oasdiff + golden fixtures + vocab lint).
- **Phase 3 (field/release):** the `User-Agent` funnel; design partners; the guarded `rc` beta ring; Discord-as-sensor.

## 8. The gap that proves the point

The dev-stack trace found that **the test suite stays green while the real TUI would crash at launch** (the design's Appendix B: `resolve_image` must reach the mount-time operator probe at `evolve_form.py:254`) — because the autouse `_hermetic_tui_discovery` stub (`conftest.py:8`) replaces *exactly* the probe that would blow up. No existing test guards "does bare `nethackers` actually open." A **Tier-1 real-TUI-launch smoke** (Pilot or the one PTY test) is the fix, and it's the concrete argument for this whole strategy: the failures that reach users are the ones current tests are structurally blind to.
