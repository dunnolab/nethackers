# gigaevo-platform analysis — for NetHackers M3

Source: cloned `AIRI-Institute/gigaevo-platform` (scratchpad). The distributed orchestration layer that runs GigaEvo at scale.

## 1. Topology
master_api (FastAPI:8000) + N runner_api containers (:8001) + Gradio web_ui (:7860) over Postgres, Kafka (KRaft), 2×Redis, MinIO, plus external `gigaevo-memory` API (:8002). **Unit of work = a whole EXPERIMENT (one full GigaEvo run), not a mutation.** Master prepares experiment files→MinIO, PUSH-dispatches via HTTP (`POST runner/…/initialize` + `/start`), runner's TaskWorker BRPOPs its private Redis queue and runs the entire evolution; results go runner→MinIO (`evolution_report.json`, 10s loop) → master's 10s poll → Postgres.

## 2. Kafka is NOT the work queue
Master produces AND consumes its only live topic `experiment-config` itself (self-loop for async file prep); 3 of 5 declared topics are dead code. Postgres (`init.sql`) = only `experiments`/`runner_instances`/`tasks` tables — **no archive/population tables** (those live in gigaevo-core's Redis, per-runner DB index).

## 3. Master vs runner split
- **Master owns:** lifecycle FSM, FIFO queue scheduler with atomic READY/BUSY runner allocation in Postgres + stale-DISPATCHING TTL recovery, runner fleet via **mounted docker.sock** (root-equivalent), results ingestion. **Master never calls an LLM.**
- **Runner owns:** the whole loop — gigaevo-core is **baked into the runner image** (`/opt/gigaevo-core`), launched as a `run.py` subprocess with Hydra overrides. **LLM mutation AND eval both happen on the runner.** Population lives in gigaevo-core's Redis.

## 4. "Evolutions" API — a second control mode (KEY validation)
`routes/evolutions.py`, `models/evolution.py` — a distinct mode for an external "CARE" client:
- `POST /api/v1/evolutions` (seed_chains, NL fitness prompt + judge model, GAConfig) →
- external GA loop `POST /{id}/individuals` (generation, chain_content, fitness_scores-per-declared-objective, parent_ids, mutation_kind) →
- `GET` list w/ pareto filter → `POST /{id}/accept` promotes winner into gigaevo-memory's `stable` channel; SSE `/events` stream; pause/resume/cancel.
**Nothing in-repo executes it — it's a passive registry (JSON objects in MinIO, prefix-scanned).** ⇒ *This is literally our thin-hub register / elite-pool pattern, already sitting inside the heavyweight platform.* Strong validation of the NetHackers architecture: compute lives with the external client; the platform is just a register + pareto-on-read + accept.

## 5. Pool / coordination
`generate_runner_pool_compose.py` emits static `runner-api-1..N` from one image; isolation = Redis **DB-index sharding** per runner; runners never self-register (master fabricates `runner-1..N` entries and health-checks them). **Sharded-by-construction: one experiment = one runner = one private population; zero cross-runner sharing/migration.** No contributor identity — single optional shared `X-API-Key`.

## 6. KEEP vs DROP (for a lean thin-hub + BYO-compute model)
**KEEP (borrow):**
- the *evolutions contract* (create-run / record-individual with objectives-subset validation, parent_ids + mutation_kind lineage, pareto-on-read, accept/promote).
- SSE snapshot-then-events for live boards.
- atomic claim + TTL recovery *if* the hub ever assigns work.
- plain-object artifact convention per run.

**DROP:**
- Kafka (self-loop, mostly dead).
- Postgres + MinIO + 2×Redis (their own evolutions mode proves object/registry storage suffices — our SQLite hub is enough).
- push dispatch + runner registry + docker.sock fleet control + compose sprawl (exist only because the platform owns compute; BYO-compute inverts this).
- master reading runners' Redis directly (`GIGAVOLVE_REDIS_URL` coupling) — keep hub↔harness HTTP-only.
- sandbox / egress-proxy subsystem (trust moves to contributors at small scale).
- Gradio UI; dual experiments/evolutions modes.

## Race caveat we already beat
`record_individual` does **unlocked read-modify-write** of the evolution record in MinIO for `best_individual_id`/`current_generation` (last-write-wins under concurrent registrants; individuals get fresh UUIDs, no idempotency/dedup). Our M3 register already has content-hash dedup (atoms natural key) + atomic best-update (SQLite transaction) — a correctness edge over their reference.
