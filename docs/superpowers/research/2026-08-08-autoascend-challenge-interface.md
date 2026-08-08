# Intel Report: `dunnolab/nethack-autoascend-challenge`

**Date:** 2026-08-08
**Analyst:** research agent (for NetHackers)
**Repo:** `dunnolab/nethack-autoascend-challenge` (GitHub, **private**; accessed via authenticated `gh`)
**Purpose of report:** Precisely characterize the co-worker's "classic harness challenge" for NetHack/AutoAscend so NetHackers can converge on shared decisions — **especially the agent interface**.

---

## 0. Access notes, provenance, and how to read this report

- The repo is **private** (`"visibility":"private"`). Topic tag: **`nethackers`** — confirms it is a sibling effort to our project.
- License: Apache-2.0. Created 2026-08-04, last pushed 2026-08-07. 0 stars, no PRs, no issues.
- **Two branches, and they materially differ:**
  - **`main`** — the plain arena skeleton. Scoring metric is raw NetHack **score**. Submission is a trivial stub `Bot`. No AutoAscend.
  - **`baseline`** — the mature line. It (a) ports **full AutoAscend** under `submission/autoascend/`, (b) adds an **`arena_adapter.py`** that bridges AutoAscend into the arena interface, (c) formalizes the interface as a `typing.Protocol` in **`bot_protocol.py`**, (d) **replaces the score metric with a BALROG-style "progress" metric** (`progress.py`), and (e) disables NLE autopickup.
- Only 2 commits total (`Initial commit`, `inital arena setup` on main; `AA cleanup` on baseline). Commit messages carry **no** design rationale — rationale lives entirely in READMEs, docstrings, and code.
- **`evaluations/` is `.gitignore`d** (along with `runs/` and `staged-submissions`), so `evaluations/example.yaml` referenced by both READMEs **does not exist in the repo** — a dangling doc link. The evaluation-YAML shape must be inferred from `scripts/prepare-evaluation.py` (which writes it) and `batch.py` (which reads it).

**Read `baseline` as their current thinking.** Where `main` and `baseline` differ I call it out; the baseline decisions are the ones to reconcile against.

---

## 1. Purpose & structure

### What it is
A **CPU-parallel judge ("arena") for symbolic NetHack bots** playing NLE's real `NetHackChallenge`, always as a **human, neutral, male Tourist** (`tou-hum-neu-mal`). Tagline (README): *"A CPU-parallel judge for symbolic bots playing NLE's real `NetHackChallenge` as a human, neutral, male Tourist."* GitHub description: *"Build symbolic NetHack agents using coding agents, program synthesis, evolution, and code-as-policy methods."* — i.e. explicitly the same problem space as NetHackers.

### Participation model
Contestants **fork the repo**, put arbitrary code under `submission/` (packages, skills, planning trees, state machines, data — any size), and expose one fixed entrypoint `submission/bot.py::make_agent()`. They **register via a PR** that adds/updates `registrations/<team-id>.yaml` pinning a fork URL + full 40-char commit SHA. A new bot version = a new PR with a new SHA. Latest registration merged before the cutoff wins.

### Top-level layout (baseline)
```
README.md                     # contest overview
Dockerfile                    # static judge image (multi-stage, uv, python 3.11.14-slim)
pyproject.toml / uv.lock      # pinned deps; nle is an optional extra
LICENSE (Apache-2.0)
docs/
  PARTICIPANT_GUIDE.md        # fork -> implement -> test -> register
  ORGANIZER_GUIDE.md          # build image, accept PRs, prepare bundle, run, publish
registrations/
  README.md                   # registration YAML template (only file tracked here)
scripts/
  prepare-evaluation.py       # clones registered forks, stages submission/, writes evaluations/DATE.yaml
  run-batch.sh                # container entrypoint; sanitizes env; runs `nethack-arena batch`
src/nethack_arena/
  __init__.py
  bot_protocol.py             # (baseline only) ArenaBot / ClosableArenaBot Protocols
  agent.py                    # AgentClient: spawns bot subprocess, wire protocol, timeouts, action validation
  environments.py             # NLEEnvironment: deterministic seeded Tourist NetHackChallenge
  progress.py                 # (baseline only) BALROG/nle-progress progression metric
  seeds.py                    # HMAC-SHA256 seed derivation
  models.py                   # TrajectorySpec / EvaluationManifest / TrajectoryResult dataclasses
  trajectory.py               # runs ONE trajectory (env<->agent loop) as its own process
  evaluate.py                 # async fan-out of trajectory subprocesses across workers; resume
  batch.py                    # evaluate many submissions -> ranked leaderboard JSON
  aggregate.py                # per-submission summary stats
  storage.py                  # atomic JSON, submission_digest, run/manifest bookkeeping
submission/                   # THE contestant slot
  bot.py                      # required entrypoint: make_agent()
  # baseline additionally ships:
  arena_adapter.py            # AutoAscend <-> arena bridge (thread + queues)
  autoascend/...              # full AutoAscend port (agent, combat, item, glyph, soko_solver, ...)
  LICENSE.autoascend
tests/                        # pytest suite incl. real-NLE test, timeout/invalid-action, batch, seeds, progress
```

---

## 2. THE AGENT INTERFACE (verbatim)

This is the crux for convergence. There are **two layers**: (A) the *contestant-facing* contract (what you implement), and (B) the *judge-side* wire protocol (how the judge drives it). Both are given verbatim.

### 2A. Contestant-facing contract

**`main` branch — `submission/bot.py` (the stub every fork starts from):**
```python
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class Bot:
    def reset(self, initial_observation: Mapping[str, Any]) -> None:
        """Start a new game and inspect its initial observation."""
        del initial_observation

    def act(self, observation: Mapping[str, Any]) -> int:
        del observation
        # Return an index into nle.nethack.ACTIONS.
        return 0


def make_agent() -> Bot:
    return Bot()
```

**`baseline` branch — the interface is formalized as a `typing.Protocol` in `src/nethack_arena/bot_protocol.py` (VERBATIM):**
```python
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol


class ArenaBot(Protocol):
    """Protocol implemented by a NetHack Arena submission agent.

    The evaluator creates one bot with ``make_agent()``, calls
    ``reset(initial_observation)`` once at the start of each episode, then calls
    ``act(observation)`` until the episode ends. Observations are mappings of
    public NLE observation keys to read-only values. Actions must be integer
    indices into ``nle.nethack.ACTIONS``.
    """

    def reset(self, initial_observation: Mapping[str, Any]) -> None:
        """Start a new episode and inspect its initial observation."""

    def act(self, observation: Mapping[str, Any]) -> int:
        """Return an integer index into ``nle.nethack.ACTIONS``."""


class ClosableArenaBot(ArenaBot, Protocol):
    """Optional extension for bots that need cleanup after an episode."""

    def close(self) -> None:
        """Release resources held by the bot process."""


def make_agent() -> ArenaBot:
    """Document the required submission factory signature."""
    raise NotImplementedError("submissions must define make_agent() in submission/bot.py")
```

**The interface, distilled:**

| Element | Signature / value |
|---|---|
| Factory | `make_agent() -> ArenaBot` (module-level in `submission/bot.py`) |
| Init/reset | `reset(self, initial_observation: Mapping[str, Any]) -> None` — called **once per episode** |
| Step | `act(self, observation: Mapping[str, Any]) -> int` — called until episode ends |
| Optional teardown | `close(self) -> None` — called when the bot process exits (`ClosableArenaBot`) |
| Action type | **`int`**, an index into `nle.nethack.ACTIONS`; validated `0 <= a < action_count`. **`bool` explicitly rejected** (`isinstance(payload, bool)` check) — so `return True` is an `InvalidAction`. |
| Observation type | `Mapping[str, np.ndarray]` — a **read-only** NLE obs dict (see keys below) |
| Lifecycle | `make_agent()` → `reset(obs0)` → `act(obs)*` → (optional) `close()`. **One agent instance handles one episode**; a fresh process/instance is created per trajectory. |
| Batching / vectorization | **None.** One bot, one env, synchronous single-env stepping. Parallelism is *across trajectories* (separate OS processes), never inside a bot. |

**Observation dict keys** (`environments.py::PUBLIC_OBSERVATION_KEYS`, identical on both branches) — this is the exact obs surface a bot sees:
```python
PUBLIC_OBSERVATION_KEYS = (
    "glyphs", "chars", "colors", "specials", "blstats", "message",
    "inv_glyphs", "inv_strs", "inv_letters", "inv_oclasses",
    "tty_chars", "tty_colors", "tty_cursor", "misc",
)
```
Note what is **absent** vs. a raw NLE obs: no `screen_descriptions`, no `tty` beyond the three above, and (critically) **no `info` dict and no `reward` are passed to the bot** — `act()` receives *only* the observation mapping. Termination/`done`, `reward`, and `info` are consumed by the judge, not the agent (see 2B). Observation arrays are made read-only (`ndarray.setflags(write=False)`, recursively) before handoff.

### 2B. Judge-side wire protocol — `src/nethack_arena/agent.py`

The bot runs in a **separate OS process** (`multiprocessing` **`spawn`** context), talking to the judge over a duplex `Pipe`. This is the actual enforcement layer for isolation, timeouts, and action validation.

Key behaviors (verbatim-sourced):
- **Process launch:** `multiprocessing.get_context("spawn").Process(target=_agent_process, ..., daemon=True)`. `bot.py` is imported *inside* that child via `importlib.import_module("bot")` after `sys.path.insert(0, submission_path)` and `os.chdir(submission_path)`.
- **Secret hygiene:** first line of `_agent_process` is `os.environ.pop("NETHACK_ARENA_SECRET", None)` — the seed secret is scrubbed from the bot's env before contestant code loads.
- **Determinism seeding of the bot:** `random.seed(bot_seed)` and `np.random.seed(bot_seed % (1<<32))` are set in the child before `make_agent()`.
- **Command loop:** parent sends `("reset", obs)`, `("act", obs)`, `("close", None)`; child replies `("ready"|"reset"|"action"|"closed"|"error", payload)`.
- **Duck-typed validation at load:** `_load_agent` requires callable `reset` and `act` (it does **not** import/enforce the `Protocol` — the Protocol is documentation + typing only).
- **Timeouts** (`AgentClient`):
  - **startup**: `max(30.0, timeout_seconds)` for the child to answer `"ready"`.
  - **per `reset` and per `act`**: `timeout_seconds` (the manifest's `action_timeout_seconds`, CLI default **5.0 s**). Exceeding it raises `BotTimeout`, kills the process, and the trajectory is scored a failure.
  - `close()` gives the bot ~1 s to acknowledge, else `terminate()` → `SIGTERM` → (2 s) → `SIGKILL`.
- **Action validation** (verbatim):
  ```python
  if isinstance(payload, bool) or not isinstance(payload, numbers.Integral):
      raise InvalidAction(f"action must be an integer, got {type(payload).__name__}")
  action = int(payload)
  if not 0 <= action < self._action_count:
      raise InvalidAction(
          f"action {action} is outside the valid range [0, {self._action_count})"
      )
  ```
- **Error taxonomy:** `BotError`, `BotTimeout(BotError)`, `InvalidAction(BotError)`. Any of these → trajectory status `bot_error` / `bot_timeout` / `invalid_action`, all scored **zero**.

### 2C. The per-trajectory driver loop — `src/nethack_arena/trajectory.py`

One trajectory = one process (spawned by `evaluate.py`). The core loop (verbatim, baseline identical to main except metric field):
```python
environment = make_environment(max_steps=..., no_progress_timeout=...)
observation = environment.reset(spec)
agent = AgentClient(submission_path, bot_seed=spec.bot_seed,
                    action_count=environment.action_count,
                    timeout_seconds=manifest.action_timeout_seconds)
agent.reset(observation)
while steps < manifest.max_steps:
    action = agent.act(observation)
    observation, _reward, terminated, truncated = environment.step(action)
    steps += 1
    if terminated or truncated:
        break
```
**Important semantics:** the **judge**, not the bot, owns episode termination. `reward` is discarded (`_reward`). The bot never sees `terminated/truncated/info`. When the env ends, the judge stops calling `act()` and calls `agent.close()`.

---

## 3. Harness / challenge mechanics

### Environment (`environments.py`)
- Real **NLE `NetHackChallenge`** subclassed as `DeterministicTouristChallenge`, constructed with:
  ```python
  character=TOURIST_CHARACTER,               # "tou-hum-neu-mal"
  observation_keys=PUBLIC_OBSERVATION_KEYS,
  allow_all_yn_questions=True,
  allow_all_modes=True,
  max_episode_steps=max_steps,               # default 1_000_000
  no_progress_timeout=no_progress_timeout,   # default 10_000
  fix_moon_phase=True,
  # baseline ALSO: options=disable_autopickup(nethack.NETHACKOPTIONS)
  ```
- **Fixed character:** always human neutral male Tourist. No role/race/objective variation.
- **Action table sanity check:** it asserts `tuple(self.actions) == tuple(nethack.ACTIONS)` at construction — the action space is exactly `nle.nethack.ACTIONS` (the full NetHackChallenge action set; `action_count = action_space.n`).
- **Seeding is judge-controlled and privileged:** `NetHackChallenge` normally blocks agent seeding; the judge calls `type(self.nethack).set_initial_seeds(self.nethack, core_seed, display_seed, False, level_seed)` directly before `reset()`. Bots cannot seed the game.
- **baseline `disable_autopickup`:** prepends `"!autopickup"` and strips any autopickup option so AutoAscend manages pickup itself. (verbatim rationale in docstring: *"Return NLE NetHack options with only autopickup switched off."*)

### Determinism & seeds (`seeds.py`)
- Per-trajectory seeds are **HMAC-SHA256** derived:
  ```python
  message = f"nethack-arena\0{evaluation_id}\0{trajectory_id}".encode()
  digest  = hmac.new(secret.encode(), message, sha256).digest()
  # four 63-bit seeds sliced from the digest:
  core_seed, display_seed, level_seed, bot_seed = _seed_part(d,0), _seed_part(d,8), _seed_part(d,16), _seed_part(d,24)
  ```
- **Consequences (stated in docs):** new `evaluation_id` **or** new secret ⇒ all seeds change; reusing both ⇒ exact reproduction (enables checkpoint resume). Seeds are **not fixed/published** — they are secret-derived and **held-out** (contestants cannot know them in advance), which resists seed-overfitting. Only the SHA-256 *fingerprint* of the secret is persisted (to detect accidental key changes); the secret never touches NFS.
- `bot_seed` seeds the bot's `random`/`numpy` global RNGs (see 2B).

### Execution & parallelism (`evaluate.py`)
- `evaluate()` builds an `asyncio.Queue` of pending `trajectory_id`s and runs `min(workers, pending)` async workers; each worker `asyncio.create_subprocess_exec(sys.executable, "-m", "nethack_arena.trajectory", ...)` — i.e. **one subprocess per trajectory** (which itself spawns the bot subprocess). So each trajectory involves **two** processes: trajectory-runner + bot.
- **CPU-parallel, not vectorized:** parallelism is process-level fan-out across the 1,024 trajectories. Default `workers = len(os.sched_getaffinity(0))`.
- **Timeouts:** each trajectory subprocess has a hard wall-clock cap `trajectory_timeout_seconds` (CLI default **3600 s**); on timeout the whole process group is `SIGKILL`ed and the result recorded as `trajectory_timeout` (zero).
- **Resume/idempotency:** results are per-trajectory JSON files (`trajectories/NNNN.json`); already-present ones are skipped, so an interrupted run resumes. `manifest.json` is written once and any later run with a different manifest (different code/settings/secret fingerprint) is **rejected** — enforced by `storage.initialize_run`.
- **Signal handling:** SIGINT/SIGTERM set a stop event; partial runs raise `InterruptedError("evaluation interrupted; rerun to resume")`.

### Scoring / metrics

**This is the single biggest change between branches.**

- **`main`:** metric is **raw final NetHack score** (`blstats[NLE_BL_SCORE]`, tracked as running max). Summary: `total_score`, `mean_score`, `median_score`, min/max, `ascensions`, `ascension_rate`, `status_counts`.
- **`baseline`:** metric is **BALROG-style "progress" ∈ [0.0, 1.0]** (`progress.py`), where 0.0 = start and 1.0 = ascension. Summary keys become `total_progress`, `mean_progress`, `median_progress`, min/max, plus `ascensions`, `ascension_rate`, `status_counts`.
  - **Official run = 1,024 trajectories.** Primary score = **mean final progression**. **Ties broken by ascensions, then median progress** (README, verbatim: *"The main metric is mean final BALROG-style progression, adapted from `nle-progress`, where 0.0 is the starting state and 1.0 is ascension. Bot errors and timeouts get zero progress. Ties are broken by ascensions, then median progress."*).
  - **Bot failures score zero** (`bot_error`, `bot_timeout`, `invalid_action` → progress 0.0). `infrastructure_error` in **any** trajectory marks the whole submission `valid: false` (i.e. an org/infra fault invalidates the run rather than penalizing the bot).

**The progress metric (`progress.py`), how it is computed:**
- A table `ACHIEVEMENTS: dict[str, float]` maps milestones to empirical ascension probabilities, *"adapted from `nle-progress` (MIT), which adapted the BALROG NetHack progression metric."* Milestones: `Dlvl:1..50` (dungeon depth), `Xp:1..30` (experience level), quest `Home 1..5`, `Astral Plane`, and `You ascend t` (=1.0).
- `NetHackProgress.update(obs, info)` records the **max** milestone value seen, reading `blstats[12]` (depth) and `blstats[18]` (experience level), scanning `message` + `tty_chars` decoded text for textual achievements, and checking `info["is_ascended"]`. It is monotonic (best-so-far).
- Ranking key in `batch.py::_rank` (baseline): `(-mean_progress, -ascensions, -median_progress, id)`.

### Result record (`models.py::TrajectoryResult`)
Fields (baseline): `trajectory_id, status, progress (float), ascended, steps, turns, max_depth, end_status, error, wall_seconds`. (`main` has `score:int` instead of `progress:float`.) `status ∈ {completed, bot_error, bot_timeout, invalid_action, trajectory_timeout, infrastructure_error}`.

### Submission / leaderboard mechanism
- **Register** via PR editing `registrations/<team-id>.yaml`:
  ```yaml
  id: alice
  name: Alice Example
  repository: https://github.com/alice/nethack-autoascend-challenge
  commit: "0123456789abcdef0123456789abcdef01234567"   # full 40-char SHA
  ```
- **`scripts/prepare-evaluation.py`** (organizer): validates unique `SAFE_ID` team ids and full 40-hex SHAs, `git clone --no-checkout` each fork, `git fetch origin <commit>`, verifies the resolved commit equals the registration, `git archive <commit> submission` → tar, then **safely extracts only the `submission/` subtree** (rejecting links/special files and `..` paths). Writes `evaluations/DATE.yaml` listing all accepted submissions + a relative `submission_root`, `work_dir` (`/nfs/work/DATE`), `output` (`/nfs/results/DATE.json`), `episodes`, `workers`.
- **`batch.py`** consumes that YAML, runs `evaluate()` per submission into `work_dir/<id>`, and writes one ranked **leaderboard JSON** with `{id, environment:"NetHackChallenge", character, episodes, complete, entries:[...ranked...]}`. Incrementally rewritten after each submission (crash-safe, resumable).
- Cutoff rule (docs): *"The latest registration merged before the evaluation cutoff is used."* Daily or weekly cadence implied (`--workers 128`, dates like `2026-08-09`).

### Docker / cluster execution
- Evaluation **runs in Docker**. Static image `nethack-arena:stable` built only when judge code / `uv.lock` changes. Daily job = read-only repo bundle + secret, no image rebuild.
- Canonical run (docs, verbatim):
  ```bash
  docker run --rm --network none --cpus 128 \
    -e NETHACK_ARENA_SECRET \
    -v "$PWD":/workspace:ro \
    -v /nfs/work:/nfs/work \
    -v /nfs/results:/nfs/results \
    nethack-arena:stable /workspace/evaluations/2026-08-09.yaml
  ```
- **`--network none`** (no networking for bots), read-only workspace, dedicated NFS for checkpoints (`/nfs/work`, private) and results (`/nfs/results`, published). Only `/nfs/results/DATE.json` is published; `/nfs/work` never is.
- `run-batch.sh` sanitizes the runtime env: fresh `HOME`/`TMPDIR`, `chmod 700`, and pins single-threaded BLAS (`OMP/OPENBLAS/MKL/NUMEXPR/VECLIB *_NUM_THREADS=1`), `PYTHONHASHSEED=0`, `TZ=UTC`, `LANG/LC_ALL=C.UTF-8` — for reproducibility and to keep 128 parallel bots from oversubscribing cores.

---

## 4. THE AUTOASCEND INTEGRATION (baseline) — the key convergence artifact

This is the most directly reusable piece for NetHackers: **how they wrap a full-program AutoAscend agent into a stepwise `act(obs)->int` interface.** It solves exactly the control-inversion problem we face.

### The problem
AutoAscend is **not** a `act(obs)->action` function. Its `Agent.main()` (`submission/autoascend/agent.py`, ~1560 lines) is an **infinite driver loop** that *pulls* the world: it repeatedly calls `self.step(action)` → `self.env.step(action)` (the **old 4-tuple Gym API**: returns `(observation, reward, done, info)`), and raises `AgentFinished()` when `done`. Constructor: `Agent(self, env, seed=0, verbose=False, panic_on_errors=False)` — it expects an `env` object exposing `.step()`, `.debug_tiles()`, `.debug_log()`. The whole program flow (exploration, combat, inventory, sokoban solver, praying, etc.) is expressed as nested strategies that call `step()` deep in the stack. You cannot cleanly turn that inside-out into a function.

### The solution: `submission/arena_adapter.py` (thread + bounded queues = coroutine bridge)
Two classes:

**`ArenaEnvAdapter`** — a fake env handed to AutoAscend. It never runs NetHack; it just brokers actions/observations across a thread boundary with two `queue.Queue(maxsize=1)`:
```python
def step(self, action):                    # called by AutoAscend on its thread
    self._actions.put(action)              # hand action out to the arena
    observation = self._observations.get() # block until arena supplies next obs
    if observation is None or self._closed.is_set():
        raise autoascend_agent.AgentFinished()
    return _copy_observation(observation), 0.0, False, {}   # 4-tuple; reward/done faked
```
It also stubs `debug_tiles`/`debug_log` as `nullcontext()` (AutoAscend calls these for visualization).

**`AutoAscendDriver`** — implements the arena `reset`/`act`/`close`, running AutoAscend on a **daemon thread**:
- `reset(initial_observation)`: **discards `initial_observation`**, spawns a thread running `Agent(env, panic_on_errors=False).main()`.
- `act(observation)`: on the first call it just pulls AutoAscend's first queued action (AutoAscend's opening `ESC`); on subsequent calls it `provide_observation(obs)` (unblocking AutoAscend's pending `env.step`) then pulls the next action. There is a deliberate **one-step pipeline lag** between arena obs and AutoAscend consumption.
- **Internal watchdog:** `AutoAscendDriver(action_timeout=4.5)` — pulls the next action with `timeout=4.5s`, **below the arena's 5.0s hard `action_timeout`**. On queue-empty / thread error it returns a **fallback action = `ESC` index** instead of hanging. So a slow/broken AutoAscend degrades to ESC rather than killing the whole trajectory.
- `close()`: sets `_closed`, pushes `None` sentinel → AutoAscend's next `env.step` raises `AgentFinished` → thread exits.

**Key design consequences worth internalizing:**
1. **Game-over authority is the arena, not AutoAscend.** The adapter always reports `done=False` to AutoAscend; termination is detected by the arena from real NLE and propagated to AutoAscend only via the `None` sentinel on `close()`.
2. **`reset()`'s `initial_observation` is dropped** by the AutoAscend path; AutoAscend gets its first *real* obs one `act()` later. Fine because turn 1 is just dismissing intro screens.
3. **AutoAscend's RNG is fixed to seed 0** (`Agent(env)` uses default `seed=0` → `np.random.RandomState(0)`, a *local* RNG). Determinism therefore comes from the NLE seed, not `bot_seed`. (The arena's `bot_seed` seeds only the global `random`/`numpy`, which AutoAscend does not use for its core logic.)
4. **`submission/bot.py` sets cache dirs before importing the adapter** (`XDG_CACHE_HOME`, `NUMBA_CACHE_DIR` under a temp dir) — because AutoAscend uses `numba` JIT and needs a writable cache under the read-only/limited-env sandbox.

---

## 5. Stated design opinions / rationale

There are no design essays; rationale is terse and embedded. The load-bearing statements:

- **Interface minimalism (README, baseline, verbatim):** *"`ArenaBot` is the official submission protocol. New bots should implement `reset(initial_observation)` and `act(observation) -> int` directly; baseline adapters may hide additional legacy conventions internally."* → They deliberately keep the *public* contract to `reset`/`act(obs)->int` and push all messiness (AutoAscend's pull-loop) into private adapters.
- **"No env ownership" rule (PARTICIPANT_GUIDE, baseline, verbatim):** *"New bots should not create or step their own NLE environment."* → The judge owns the env; bots are pure policies over the provided obs.
- **Arbitrary bot complexity is explicitly welcomed (README):** *"A submission can be a large codebase with packages, skills, planning trees, state machines, and data. Only `submission/bot.py` is fixed as the entrypoint."* → aligns with full-program symbolic agents.
- **Held-out secret seeds (docs):** HMAC-derivation is chosen so *"A new evaluation ID changes every seed... Reusing both reproduces the same run for checkpoint resume,"* and *"The secret itself is never written to NFS."* → anti-overfitting + reproducible resume + secret hygiene as first-class goals.
- **Image/bundle separation (ORGANIZER_GUIDE):** *"The judge Docker image is static. Build it only when judge code or `uv.lock` changes."* → cheap daily evals, expensive-image discipline.
- **Security posture is explicitly incomplete (ORGANIZER_GUIDE, verbatim):** *"the bot is not yet in a separate adversarial sandbox from the judge. Disable networking, provide no credentials, and use a dedicated cluster account. Add a stronger bot sandbox if hostile submissions are in scope."* → they know process isolation ≠ adversarial sandbox; they rely on `--network none` + secret-scrubbing + dedicated account.
- **Progress over score (baseline):** switching the metric to BALROG progression signals they value *how far toward ascension* over raw score-farming — a smoother, more comparable signal across weak bots.

---

## 6. Dependencies & packaging

- **Language/runtime:** Python **>=3.11**; Docker pins **`python:3.11.14-slim-bookworm`**. Built with **`uv`** (`ghcr.io/astral-sh/uv:0.9.15`), `--frozen` from `uv.lock`, `--no-editable`, bytecode-compiled.
- **NLE:** **`nle==1.3.0`** (optional extra `nle`). *(NLE 1.3.0 bundles NetHack 3.6.6.)* The judge image installs the `nle` extra; contestant deps are **not** separately installed.
- **Judge base deps:**
  - `main`: just `PyYAML==6.0.3` (+ dev `pytest==8.4.2`, `ruff==0.12.12`).
  - `baseline`: **AutoAscend's runtime deps are baked into the judge image** — `nltk>=3.10.2`, `numba>=0.66.0`, `opencv-python>=5.0.0.93`, `scipy>=1.17.1`, `toolz>=1.1.0`, plus `PyYAML==6.0.3`. (uv.lock also resolves `llvmlite`, `numpy`, `pybind11`.)
    - **Implication:** there is **no per-submission dependency mechanism**. A submission cannot ship its own `requirements`/`pyproject`; whatever it imports must already be in the judge image. AutoAscend's deps were added to the *arena's* `pyproject.toml`. This is a real coupling: new bot dependencies require an image rebuild by the organizer.
- **Packaging of a submission:** source-only. `prepare-evaluation.py` `git archive`s the fork's `submission/` subtree and stages it; `bot.py` imports anything under `submission/` (its dir is on `sys.path`, cwd is set to it). No wheels, no build step for the bot. `submission_digest` (SHA-256 over sorted file contents, ignoring `.git/.venv/__pycache__/...`) records exactly what was evaluated.
- **Docker:** multi-stage (uv builder → slim runtime). Runtime env pins single-threaded BLAS + `PYTHONHASHSEED=0` + UTC/C.UTF-8. Entrypoint `scripts/run-batch.sh`. Run with `--network none`, `-v $PWD:/workspace:ro`.

---

## 7. Convergence analysis (vs. NetHackers)

### 7.1 Where it ALIGNS with NetHackers
- **Full-program symbolic agents over AutoAscend.** Same north star. Their baseline *is* AutoAscend wrapped as a submission; contestants ship "large codebases with packages, skills, planning trees, state machines." This matches NetHackers' "deterministic symbolic player programs built on/around AutoAscend."
- **Seeded, deterministic NLE evaluation.** They evaluate on seeded `NetHackChallenge` episodes with fully reproducible, resumable runs. Same evaluation philosophy as our "seeded NLE episodes, deterministic evaluation."
- **AutoAscend integration pattern already solved.** Their `arena_adapter.py` (thread + bounded-queue coroutine bridge, fallback-ESC watchdog, cache-dir setup, `disable_autopickup`) is a **drop-in reference** for the exact control-inversion problem NetHackers has when turning AutoAscend into a stepwise policy. **We should adopt or at least mirror this bridge.**
- **Evidence/progress signal.** Their baseline `progress.py` (BALROG/nle-progress milestone→ascension-probability) is a ready-made, citable progression metric — conceptually adjacent to NetHackers' "evidence-tiered scoring." Milestones (Dlvl, Xp, quest Home, Astral Plane, ascension) are a natural evidence ladder.
- **Determinism hygiene:** privileged judge-side seeding, RNG seeding of the bot, single-threaded BLAS, `PYTHONHASHSEED=0`, secret-scrubbing — all good practices we likely want too.

### 7.2 Where it DIVERGES (things we must reconcile on the interface)
1. **Action representation: `int` index into `nle.nethack.ACTIONS` vs. NetHackers' likely richer action abstraction.** Their public contract is a single integer per step — the lowest-common-denominator NLE action. This is the **#1 interface decision to reconcile.** If NetHackers wants higher-level "objectives"/skills or keystroke sequences at the interface, that conflicts with their `act(obs)->int`. (Note their AutoAscend path internally emits many low-level keys per "decision" — the int-per-step interface forces a per-keystroke cadence, which is why they need the threaded coroutine bridge and a 4.5–5.0s per-step budget.)
2. **Observation surface is fixed to 14 NLE keys, read-only, and `act()` gets *only* the obs.** No `reward`, no `info`, no `done`/`terminated` to the bot; no `screen_descriptions`. If NetHackers passes reward/info/objective context into the agent, that diverges. Also they hand bots the raw NLE dict (glyphs/chars/blstats/tty/message/inventory arrays) — no pre-parsed/structured state. Reconcile: do we standardize on the raw NLE obs dict, or a richer parsed observation?
3. **One character, one objective.** They hardcode `tou-hum-neu-mal` and there is **no per-character / per-objective structure at all** — every episode is the same Tourist with the single implicit objective "ascend (progress toward Astral)." NetHackers' **per-character objectives** have no analog here. This is a genuine gap to reconcile: our evaluation is multi-character/multi-objective; theirs is single-character/single-metric.
4. **Scoring model.** They use a **single scalar** (mean progression, ties→ascensions→median). NetHackers' **evidence-tiered** model is richer. Their `progress.py` gives us a good *within-tier* progression signal, but there is no notion of evidence tiers, confidence, or per-objective credit. Reconcile: is progression one tier of our evidence stack, or a competing top-line metric?
5. **Lifecycle granularity: per-episode agent, no cross-episode state, no batching.** `make_agent()`→`reset`→`act*`→`close`, one episode per instance, one env per bot, parallelism only across processes. If NetHackers wants a persistent agent across episodes, vectorized/batched stepping, or a harness that itself drives multiple envs per agent, that diverges.
6. **Per-step time budget = 5.0 s hard (4.5 s soft) per `act()`; trajectory cap 3600 s; `max_steps` up to 1e6.** These are concrete numbers we'd have to match or consciously differ from. Their per-step budget is generous enough for symbolic planning but assumes *one NLE action per call*.
7. **No per-submission dependency isolation.** Bot deps must live in the judge image (AutoAscend's numba/opencv/scipy/nltk were added to the arena's own `pyproject.toml`). NetHackers, if it wants heterogeneous evolved programs with varying deps, needs a different packaging story (per-submission venv / container), which theirs does not provide.
8. **Registration/versioning is human-in-the-loop via GitHub PRs + commit SHAs.** NetHackers is described as LLM-driven/automated evolution; a PR-per-version cadence would not scale to evolutionary search. We'd diverge toward programmatic submission.

### 7.3 What NetHackers should consider ADOPTING from them
- **The `arena_adapter.py` coroutine bridge** (thread + `Queue(maxsize=1)` + fallback-ESC watchdog + `AgentFinished` sentinel + numba cache-dir setup + `disable_autopickup`) — near-verbatim reusable for running AutoAscend as a stepwise policy. This is the single highest-value takeaway.
- **HMAC-derived secret seeds** (`nethack-arena\0{eval_id}\0{traj_id}`) for held-out, reproducible, resumable seeding without publishing seeds; persist only the secret's SHA-256 fingerprint.
- **`bot_protocol.py` as a `typing.Protocol`** — a clean way to *document* the interface without importing it into contestant code (validation stays duck-typed). Good pattern if we want a formal-but-loose contract.
- **Process-isolation + timeout + action-validation harness** (`agent.py`): spawn subprocess, secret-scrub, per-call timeouts with escalating SIGTERM/SIGKILL, explicit `bool`-rejecting integer action validation, and the failure taxonomy (`bot_error/bot_timeout/invalid_action/trajectory_timeout/infrastructure_error` with `infrastructure_error` invalidating the run rather than penalizing the bot).
- **`progress.py` BALROG progression metric** as at least one evidence tier / smoother-than-score signal. It also cleanly detects textual milestones from `message`+`tty_chars` and ascension from `info["is_ascended"]`.
- **Reproducibility env pins:** single-threaded BLAS across all libs, `PYTHONHASHSEED=0`, UTC/C.UTF-8, atomic JSON writes, per-trajectory resumable checkpoints, and a manifest that refuses to resume if code/settings/secret changed.
- **Static image + read-only job bundle** split for cheap frequent evals.

### 7.4 The specific interface decisions to put on the shared-decision table
1. **Action granularity:** commit to `int`-index-into-`ACTIONS` per `act()` (their choice) **or** a higher-level action/skill/objective interface (likely ours). This drives everything else (step budget, adapter design, obs cadence).
2. **Observation contract:** exact key set; raw NLE arrays vs. parsed state; whether `reward`/`info`/`done`/objective-context are exposed to the agent (they expose none).
3. **Lifecycle:** `make_agent/reset/act/close`, one-episode-per-instance, no batching — accept as the shared minimal contract, or extend for persistence/vectorization/objectives.
4. **Multi-character / per-objective:** they have none. If shared, we must extend the manifest/spec (`TrajectorySpec`/`EvaluationManifest` are the natural extension points — add role/race/objective) and the metric (per-objective credit) — theirs currently hardcodes Tourist + single progression scalar.
5. **Scoring/evidence:** whether progression is *the* metric (theirs) or *one tier* of an evidence-tiered model (ours), and how ascensions/median tie-breaks map onto our tiers.
6. **Packaging & submission:** GitHub-PR + SHA + baked-in deps (theirs) vs. programmatic/automated submission with per-agent dependency isolation (likely ours).
7. **Step/trajectory/time budgets:** 5.0 s/act, 3600 s/trajectory, 1e6 max_steps, 1,024 episodes — adopt or diverge deliberately.

---

## 8. Gaps, caveats, and things NOT found
- **`evaluations/example.yaml` does not exist** (gitignored) despite being linked from both READMEs. The evaluation-YAML schema is only inferable from `prepare-evaluation.py`/`batch.py`. (Inferred shape: `{id, submission_root, work_dir, output, episodes, workers, submissions:[{id,name,repository,commit,directory?}]}`.)
- **No registrations, PRs, or issues yet** — the contest hasn't run; no leaderboards or example submissions beyond the AutoAscend baseline.
- **`main` vs `baseline` divergence is unmerged.** It is unclear which they consider canonical, but `baseline` is strictly more advanced (AutoAscend + progress metric + Protocol) and README wording there is the more considered. Treat `baseline` as their direction; confirm with the co-worker which branch is authoritative.
- **Security is explicitly not adversarial-sandboxed** (their own note). Process isolation + `--network none` + secret-scrub only.
- **No per-objective, per-character, or evidence-tier machinery exists** — the biggest structural gap vs. NetHackers, and the area most needing a joint decision.
- The `main`-branch README still says "mean final NetHack **score**"; the `baseline` README says "mean final **progression**." The metric is genuinely in flux between branches.

---

## Appendix: quick-reference constants (baseline)
- Character: `TOURIST_CHARACTER = "tou-hum-neu-mal"`, `TOURIST_ROLE = "Tourist"`
- `OFFICIAL_EPISODES = 1024`, `DEFAULT_MAX_STEPS = 1_000_000`, `DEFAULT_NO_PROGRESS_TIMEOUT = 10_000`
- CLI defaults: `--action-timeout 5.0`, `--trajectory-timeout 3600.0`, `--workers = CPU affinity count`, `--seed development`, `--evaluation-id local`, `--result-dir runs/local`
- Adapter internal: `AutoAscendDriver(action_timeout=4.5)`, fallback action = `ESC` index
- NLE `1.3.0` (NetHack 3.6.6), Python `3.11.14`, uv `0.9.15`
- blstats indices used: `[9]=score` (main), `[12]=depth`, `[18]=experience_level`, `[20]=time` (via NLE_BL_* constants)
- Obs keys (14): glyphs, chars, colors, specials, blstats, message, inv_glyphs, inv_strs, inv_letters, inv_oclasses, tty_chars, tty_colors, tty_cursor, misc
