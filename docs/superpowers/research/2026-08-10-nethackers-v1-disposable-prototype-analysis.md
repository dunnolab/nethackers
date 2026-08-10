# Disposable v1 (`dunnolab/nethackers`) analysis — for NetHackers M3

Source: `~/nethacker-evolution/nethackers`, package `src/nethacker/`. ~5 commits, 2026-07-31→08-02. src/nethacker = 7,138 LOC / 16 modules (workflow 1249, evaluation 1008, federation 978, cli 742, runner 600) + 1,974 test LOC. Vendored gigaevo-core = 13,063 LOC. Extras: langchain-openai, langgraph, hydra, fakeredis, redis.

## 1. What it evolved (THE crux) — triple indirection
Candidate = JSON: `{schema_version:"nethacker.candidate/v2", name, description, baseline{repo:"autoascend", commit:fe3c9a2…}, parents[≤2 sha256 digests], patch:<git unified diff string>}` — a **whitespace-exact git diff against a pinned 15-file AutoAscend root whitelist** (candidate.py:25-43,179-187), ≤256KB/≤32 files, no renames/binaries/deletes, must pass `git apply --check --whitespace=error-all` (candidate.py:375-389).
BUT gigaevo only evolves "Python programs with `entrypoint()`", so the LLM actually rewrites a **carrier .py whose `entrypoint()` returns that dict** (initial_programs/autoascend.py). ⇒ **triple indirection: Python → dict literal → diff string → bot source.** Whitespace-exact `git apply` failures are a whole rejection class. THIS is disposability reason #1.

## 2. AutoAscend bridge (it's runner.py, not bridge.py)
`materialize_candidate` copies whitelisted files to temp + `git apply` (evaluation.py:219-232). `run_episode` sys.path-inserts+chdirs the patched root, `from agent import Agent`, wraps NLE `NetHackChallenge-v0` in a duck-typed `BotEnvironment` shim (.step/.score/.step_count/.visualizer/.debug_tiles), seeded, `agent.main()` under a ctypes `PyThreadState_SetAsyncExc` stall watchdog (runner.py:233-421). Candidates may rewrite any of the 15 symbolic modules wholesale.
`bridge.py` is actually an **OpenAI-compatible localhost HTTP shim over `codex exec`/`claude --print` subprocesses** (subscription auth, fake token counts, tool-call emulation) fed to gigaevo as `OPENAI_API_KEY=<bridge token>`.

## 3. Loop + gigaevo integration
`workflow.run_ascend` = 415-line monolith (workflow.py:836-1249): embedded **fakeredis TCP server** + bridge + `python -m nethacker.gigaevo_runtime` subprocess with ~15 hydra override strings (incl. MAP-Elites behavior_space `[mean_depth_norm, mean_branch_coverage, crash_rate, is_valid]` and a `max_generations=N+1` hack). Progress scraped from stdout via regex into a rich Live TUI. `gigaevo_runtime.py` stubs gigaevo's tensorboard/langfuse via sys.modules injection.
Eval: problem's 5-line validate.py → `validate_for_gigaevo` → Docker **two-container anti-cheat**: locked-down policy container emits base64 action trace (trace_supervisor.py), a second trusted container replays it to recompute metrics. Fitness = `0.65·progress + 0.25·log-score + 0.10·ascension − 0.30·crash`. Profiles smoke/dev/submission (2→52 episodes, forced character panels, sha256-derived seeds). Archive = read gigaevo's RedisProgramStorage, rank by (is_valid, fitness, progress, median), export top-10 JSON + improved/regressed log.

## 4. Federation (confirmed)
Transport = each contributor's GitHub repo, name-enforced `<owner>/nethacker`. Every round **replaces the whole repo snapshot** via raw Git Data API blobs→tree→commit: result.json, generated viral README (ASCII art + badges + `./nethacker --from <repo>` join), candidate.json, pinned Dockerfile, standalone run.py (made by string-replacing an import), materialized autoascend/*.py.
**Ancestors-as-generated-Python confirmed**: hub pool entries written into `problems/…/initial_programs/network-NN-<status>-<digest>.py` as `CANDIDATE={…}; def entrypoint(): return CANDIDATE` — the only way to inject JSON into gigaevo's Python-population contract. Hub (separate service, not in repo) does pool/reputation/badges/canonical verification on HMAC hidden seeds; 3-tier trust verified/provisional_trusted/self_reported. bootstrap.py = a wheel that git-clones its own repo into ~/.cache pinned by a git-index digest.

## 5. Why disposable → lean alternative
- **(a) Representation triple-indirection** (LLM writes diffs blind inside a dict inside Python; whitespace-exact apply = rejection class) → **evolve the artifact itself** (real file tree / commit the agent edits, evaluated as-is).
- **(b) ~2,000 LOC vendor adapters** (fakeredis server, telemetry sys.modules stubs, OpenAI-emulating HTTP bridge, hydra string overrides, stdout regex, N+1 hack) to avoid touching 13k-LOC gigaevo → **own the ~200-line mutate→eval→select loop**; no Redis/hydra/langchain.
- **(c) Anti-cheat before users existed** (dual-container trace replay, hidden-seed canonical suite, reputation tiers, byte-exact snapshot validation, criteria DSL, runtime digest pins, 7 schema constants + a *legacy* schema in a 3-day-old repo) → **trust contributors; hub re-runs top candidates; add provenance when cheating actually appears.** (Matches our M2a regime discipline: at small scale the binding constraint is search working + retention, not verification.)
- **(d) run_ascend monolith** coupling TUI+Docker+GitHub+hub+subprocesses + ~500-line hand-rolled urllib GitHub client doing blob-level commits → **hub owns coordination; client is thin submit/fetch; plain prints.**
- **(e) Distribution gymnastics** (self-cloning bootstrap wheel, per-candidate generated Dockerfiles, source string-replace) → **one repo, `uv run`, one Dockerfile.**

## Worth KEEPING
- candidate digest = sha256(canonical JSON) as global ID.
- pinned-baseline + patch as the lineage/exchange format (compact, diffable) — *note tension with (a); reconcile in M3*.
- deterministic seeded eval w/ fixed character panels + tiered budgets (smoke/dev/submission).
- progress-shaped fitness (depth/branch-coverage/crash, not raw score).
- action-trace replay as a cheap verification *idea* (defer).
- experiment log with hypothesis + improved/regressed outcomes.
- network ancestors seeding the initial population.
- the viral GitHub-identity / README layer.
