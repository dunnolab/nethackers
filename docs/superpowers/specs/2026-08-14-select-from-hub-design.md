# SELECT-from-hub — compounding evolution via a trust-aware elite parent (design)

**Status:** Approved in brainstorming 2026-08-14; implementation next (full cycle). Builds on the merged run-isolation + hermetic-operator + parallel-eval line (`main`).

## 1. Problem

The loop only **writes** to the hub (`register_win`); it never **reads** it for SELECT. Every run cold-starts from `--seed` (AutoAscend), so registered wins pile up in the hub **unused** and **evolution never compounds**. The M3 spec §SELECT intended the opposite — *parent = the objective's top elite; cold-start from AutoAscend only if none* — but the MVP shipped only the write half.

## 2. Goal & scope

Make SELECT pull the objective's **top trusted elite** from the hub and start the run from it (so runs compound), served through a content-addressed **local byte-cache**, with a **trust model** correct for the eventual multi-contributor world. Fall back to `--seed` only when there is genuinely nothing trusted to build on.

**In scope (MVP):**
- shared machine-wide content-addressed store (moved out of the per-run dir);
- `select_parent` = hub-index + local-byte-cache resolver, trust filter, **top-k sampling** (exploit↔explore, default `k=1`), `--seed` fallback;
- elite entries carry `{digest, score, tier, owner, repo, commit_sha}`;
- CLI knobs `--select-k`/`--select-temp` (sampling) and `--from-seed`; the trust policy is **fixed** (`verified ∪ self-reported-owned-by-me`, no knob — §3/§4.2);
- the `held-out` → `validation` rename (naming honesty for the seed model);
- **eval batch: 15 episodes/identity** (was 8) — reduce variance, closer to the ~1024-episode reliable standard;
- the cache-miss `fetch` is wired (git `pull`) but never fires for your own wins.

**Out of scope (deferred; seams left in place):**
- **M2b verification** — the hub re-running on a *secret held-out* seed set to stamp `verified`. This spec only reserves the seam (`tier` on entries, `trust` policy) and the secret seed space.
- multi-contributor distribution / auth / real remote repos.

## 3. The trust & seed model this rests on

**Seeds are different dungeons**, so a score on one seed set does not transfer to another. There are **three seed tiers**:

| tier | seeds | visible to | scored by | measures |
|---|---|---|---|---|
| **train** | published batch (`secret="public"`, traj 0..N) | evolver | evolver | fit on dungeons it optimized against |
| **validation** | evolver-side reserved (today the 1000.. range) | evolver | evolver | the evolver's own overfit gate — still evolver-derivable |
| **held-out** | derived from a **secret verifier key** | **only the hub** | **hub** | fit on dungeons no one could tune to |

So the two evidence tiers are scores on **different dungeon sets**, not the same eval run twice:
- **`self-reported`** = train/validation (dungeons the evolver could see). Provenance = the owner, on the owner's compute — the hub takes their word for it. Honest ≠ verified: an honest win and a fabricated 0.99 arrive identically.
- **`verified`** = the **secret held-out** (dungeons the solution never saw), re-run by the hub. Trustworthy for two independent reasons: **neutral reproduction** *and* **unseen-dungeon generalization**.

**Trust is relative to the observer**, and it's a *set*, not a binary:
> **trusted = `verified` (by anyone) ∪ `self-reported` owned by *me* (or owners I trust)**

Your own win is objectively `self-reported` (the hub hasn't reproduced it), but **your** selection trusts it because you own it. Strangers won't trust it until the hub verifies it. Lifecycle: `self-reported`(mine) → *[hub re-runs on secret held-out]* → `verified`(everyone's). For a single contributor, this set collapses to "everything I made."

## 4. Design

### 4.1 Two-tier store: hub is the index, local store is the byte-cache
- **The hub is always the authority on *which* elite is current.** SELECT queries it every run, so a win registered since (yours from another run, or a contributor's) shows up immediately.
- **The local store is a pure content-addressed byte-cache.** Because a digest is immutable, the cache never goes stale and needs no invalidation — it only answers "do I already have these bytes."
- The tree store **moves out of `runs/<id>/trees/` into one shared `~/.nethackers/evolve/store/`** (dedup by digest, shared across all runs on this machine). Per-run dirs keep `work/` + `metrics.jsonl` + `logs/` + `run.json`. Results stay isolated (metrics record *which digest* each run produced/won); only the immutable blob cache is shared — which is correct.

### 4.2 `select_parent` — the resolver
New pure, injectable module `src/nethackers/harness/select.py`:
```python
def select_parent(
    hub, objective: str, store: LocalTreeStore, seed_tree: Path, *,
    owner: str,
    k: int = 1, temperature: float = 1.0, rng: random.Random | None = None,
    fetch: Callable[[dict, Path], Path | None] = pull_fetch,
) -> tuple[Path, str | None]:
    """Resolve the parent to evolve from. Returns (tree_path, elite_digest) —
    elite_digest is None when we fell back to the cold-start seed."""
```
Flow:
1. `entries = hub.elites(objective)` — each `{digest, score, tier, owner, repo, commit_sha}`. Any hub error → `(seed_tree, None)` (cold start).
2. Keep the **trusted** entries — a *fixed* policy: `tier == "verified"` **OR** `owner == <me>` (you always trust your own eval). None trusted → `(seed_tree, None)`. There is **no `--trust` knob**: `verified`-only can't build on your own fresh wins (and pre-M2b there are no verified scores, so it would never compound), and "trust anyone" is just the poisoning risk — the only sensible widening is an *owner allowlist* (`--trust-owners`), deferred with multi-contributor (§7).
3. **Choose among the top-k trusted (exploit↔explore):** take the `k` highest-scoring trusted entries; `k == 1` → the single best (pure exploit, deterministic — the old top-1). `k > 1` → **sample one** with probability `∝ exp(score / temperature)` (softmax) using the run's seeded `rng` — `temperature → 0` recovers argmax, larger flattens toward uniform. This is MAP-Elites' fix for the top-1 monoculture/takeover the M2a elite-pool design called out (top-k retention exists precisely so search can sample it).
4. **Serve the bytes through the cache:**
   - `store.has(top.digest)` → `(store.path(top.digest), top.digest)` (hit — always the case for your own wins).
   - miss → `fetch(top, tmp)`; on success `got = store.save(tmp)`; **integrity check** `got == top.digest` (content-addressing verifies the pulled bytes match the claimed digest); then return the cached path. Any failure / digest mismatch → `(seed_tree, None)`.

### 4.3 Cache-miss `fetch`
```python
def pull_fetch(entry: dict, dest: Path) -> Path | None:
    try:    return pull(f"{entry['repo']}@{entry['commit_sha']}", dest)
    except Exception:  return None
```
Uses the existing `hubclient.pull`. For a synthetic local pointer it fails cleanly → fallback; for your own wins it never runs (cache hit); for real remote wins it's the multi-contributor path. Integrity is enforced by §4.2's digest check, so a wrong/hostile tree can't be accepted.

### 4.4 CLI + loop wiring
- **`cli.py` `evolve`:** build the shared store `store = LocalTreeStore(Path(args.workdir) / "store")`; resolve the parent `parent_tree, parent_digest = select_parent(hub, args.objective, store, Path(args.seed), owner=args.owner, k=args.select_k, temperature=args.select_temp, rng=<Random seeded from the run-id>)`; record `parent` (= `parent_digest or "seed"`) in `run.json`; call `run_loop(seed_tree=parent_tree, tree_store=store, ...)`.
- **`run_loop` is otherwise unchanged** — it already takes `seed_tree` + `tree_store` and re-evals the seed at cold-start, which now (correctly) evaluates the *selected parent* to establish the baseline the run must beat.
- New args: `--select-k` (default `1` = exploit; sample among the top-k trusted); `--select-temp` (default `1.0`; softmax temperature, used only when `k>1`); `--from-seed` (force the cold-start seed, ignoring the hub — for deliberately starting a fresh line). **No `--trust` knob** — the policy is fixed (§4.2). The sampling RNG is seeded deterministically per run (from the run-id) so the parent choice is reproducible; `run.json` records the chosen `parent` digest plus `{select_k, select_temp}`.
- The cold-start report/metric names the parent (`elite=<digest>` vs `seed`).

### 4.5 `held-out` → `validation` rename
Reserve "held-out" for the verifier-secret tier by renaming the current evolver-side gate:
- `harness/seeds.py`: `heldout_spec` → `validation_spec` (start range unchanged).
- `harness/loop.py`: `held*` locals/reports → `validation*`; `IterationResult.heldout_fitness` → `validation_fitness`; `EliteState.heldout_fitness` → `validation_fitness`.
- `harness/runlog.py`: the `metrics.jsonl` field `heldout_fitness` → `validation_fitness`.
- `cli.py`: `--heldout-n` → `--validation-n`.
- update the touched tests. Mechanical; its own task/commit.

### 4.6 Hub `/elites` entries must carry `{tier, owner}`
The trust filter (and the M2b seam) needs `tier` and `owner` on each elite entry. If `views/elites.read_elites` / `GET /elites` doesn't already return them, add them (small). MVP values: `tier="self-reported"`, `owner=<you>` — so the fixed `verified ∪ mine` policy keeps all your entries.

### 4.7 Eval batch size — 15 episodes per identity
`hub/objectives.py`'s `per_identity_size` **8 → 15** (and the CLI validation default to match). Eight episodes is too noisy — that small-batch variance is exactly the "train up, validation flat" effect behind the original debugging; 15 roughly halves the standard error and is cheap now that eval runs in parallel, while still a pragmatic fraction of the sibling arena's 1024-episode reliable setting. **Implication:** changing a batch changes that objective's `digest`, so prior atoms/elites sit under the *old* (8-episode) objective — the first post-bump run finds nothing trusted for the new objective and cold-starts from `--seed`, building a fresh, more-reliable line. Fine for the MVP.

## 5. Concrete flows

- **Single contributor (today):** hub top for `val-dwa-law-fem` = your `a15e66` (self-reported 0.136). `store.has` → hit. Parent = `a15e66`; the run compounds from 0.136, not AutoAscend's 0.089.
- **Fresh win mid-flight:** run 2 (another terminal) just registered `b7` (0.15); starting run 3, SELECT queries the hub and starts from `b7` — the always-ask-the-hub split gives this for free.
- **Multi-contributor stranger:** stranger X's `c9` claims 0.99 (self-reported). The `verified ∪ mine` policy **drops it** (not verified, not yours); you start from your own best. X's claim is ignored no matter how high.
- **Verified lifecycle (M2b):** the hub re-runs `c9` on its secret held-out → 0.05 (refuted, drops); re-runs your `m4` → confirmed → `m4` becomes `verified`, now trusted by everyone. SELECT code doesn't change.

## 6. Testing (unit, no Docker/NLE)

- `select_parent` with a fake hub + real `LocalTreeStore(tmp)`:
  - top self-reported-mine, cache hit → returns `(store.path(d), d)`;
  - cache miss → injected `fetch` writes a tree, `store.save` matches the claimed digest → returns cached path; digest **mismatch** → `(seed, None)`;
  - hub raises → `(seed, None)`; empty pool → `(seed, None)`.
- trust filter (fixed `verified ∪ mine`): drops a stranger's self-reported entry, keeps a verified stranger + a self-reported-mine and picks the top-scoring of those; a pool of only strangers' self-reports → `(seed, None)`.
- top-k: `k=1` → deterministic argmax (identical to the old top-1); `k>1` with a seeded `rng` → the chosen parent stays within the top-k trusted set, a fixed seed reproduces the choice, and lowering `temperature` concentrates on the best-scoring; sampling never escapes the trust filter.
- `pull_fetch` returns `None` on a `pull` error (injected runner).
- CLI: `evolve` builds `store` under `<workdir>/store`, calls `select_parent`, records `parent` in `run.json`, passes the resolved tree + shared store to a stubbed `run_loop`; `--from-seed` bypasses the hub (parent = `--seed`, `parent="seed"`).
- rename: touched tests updated; `metrics.jsonl` now carries `validation_fitness`.

## 7. Deferred / seams / M2b

- **Verification (M2b):** the hub derives a **secret held-out** seed set from a private key (never `public`, never published — the sibling arena's HMAC-secret model) and re-runs registered wins to stamp `verified`. This spec **reserves** that: `tier` on entries, the `trust` policy, and a promise never to publish or `public`-derive the verifier's range.
- **trust others' self-reports** — a `--trust-owners` allowlist to widen trust beyond `verified ∪ mine` (only meaningful once other contributors exist).
- multi-contributor distribution: real remote repos + auth so `pull_fetch` fires for others' wins.
