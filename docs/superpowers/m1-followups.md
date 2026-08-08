# M1 → M2 follow-ups

Deferred items from the M1 whole-branch final review (verdict: ready to merge). None block M1; tracked here for M2 scoping.

- `Evidence.evaluator_image` currently stores the image **tag** (`nethackers/arena:dev`); spec §5 wants the image **digest** in every Evidence record — upgrade to digest for tier-2/3 verification.
- `trajectory_timeout` `ResultStatus` is declared but **unreachable** in M1 (`run_trajectory` returns `completed` on natural end or on max_steps/truncation); add a per-trajectory wall-clock timeout for M2 (hub runs untrusted bots). NLE's `max_episode_steps` + `no_progress_timeout` + the per-act 5s timeout are the M1 backstops.
- `environment.py`'s `max_depth` uses `NLE_BL_DLEVEL` (branch-relative) while `progress.py` keys `Dlvl:` off `blstats[12]` = `NLE_BL_DEPTH` (absolute); they diverge in branches. `max_depth` is diagnostic-only (not used in ranking), but confirm intent and align to `NLE_BL_DEPTH` if "deepest reached" is meant.
- Test gaps to add: `Evidence.from_results` empty-results branch; `test_models` digest stability across two independently-constructed instances; `test_seeds` ValueError paths (empty secret / negative id); `cli --image` override threading.
- Swap `tests/test_bot_protocol.py`'s private `typing._get_protocol_attrs` for a version-guarded public check.
- Add a minimal CI workflow (fast `pytest -m "not nle and not docker"` + `mypy src/nethackers tests` + `ruff check`) to lock the gates.
- mypy's `bot` override matches by bare module name (inert today; note the breadth).
