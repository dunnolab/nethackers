# Contributing

```
   ___         _       _ _         _   _
  / __|___ _ _| |_ _ _(_) |__ _  _| |_(_)_ _  __ _
 | (__/ _ \ ' \  _| '_| | '_ \ || |  _| | ' \/ _` |
  \___\___/_||_\__|_| |_|_.__/\_,_|\__|_|_||_\__, |
                                             |___/
```

There are three quite different ways to contribute, and only one of them
involves this repository.

| You want to | Do this |
|---|---|
| Improve a bot / climb the board | [`../README.md#quickstart`](../README.md#quickstart) — no repo changes needed |
| Build your own harness | [`harness.md`](harness.md) — again, no repo changes needed |
| Change the CLI, hub, arena, or docs | Read on |

Bots and harnesses are the point of the project; they arrive as `repo@commit`
registrations, not pull requests. Nothing below applies to them.

---

## Development setup

```bash
git clone https://github.com/dunnolab/nethackers
cd nethackers
uv run nethackers --help          # resolves the env on first run
```

Python 3.11+. [`uv`](https://docs.astral.sh/uv/) manages the environment; there
is no separate install step.

`uv run nethackers ...` always uses live source, and is what you want for
iteration. Path installs (`uv tool install --from .`) used to serve a stale wheel
that ignored your edits; `[tool.uv] cache-keys` in `pyproject.toml` now keys the
cache on `pyproject.toml` and `src/**/*.py`, so Python changes are picked up. Note
what those keys do *not* cover: non-`.py` assets such as
`src/nethackers/hub/web/index.html`. `make install` still runs `uv cache clean`
first, which is the blunt fix if an install ever looks stale.

## Tests

```bash
make test     # fast suite: no NLE, no Docker, no live agent CLIs
make check    # mypy + ruff
make smoke    # the full `-m docker` suite (needs built arena+mutator images)
```

The suite is marker-partitioned so the fast path stays fast:

| Marker | Needs |
|---|---|
| `nle` | the optional NLE dependency and a real NetHack environment |
| `docker` | Docker and the built arena image |
| `codex_live` | the real `codex` CLI plus a login |
| `claude_live` | the real `claude` CLI plus network |
| `github_live` | the real GitHub App plus network |

`make test` excludes all of them except `github_live`; CI excludes that one too,
and adds mypy, ruff, and a compose smoke job. (No test carries `github_live`
today — it is reserved.)

Style: ruff with `E,F,I,UP,B,SIM` at line length 100; mypy over `src/nethackers`
and `tests`. Both are enforced in CI, so run `make check` before pushing.

## Running against a real hub

Unit tests are not enough for anything touching boards, registration, or the
web page. Bring up an isolated local stack:

```bash
make hub                    # stub auth + fixtures, offline, seconds to start
make hub HUB_AUTH=github    # real GitHub auth against an empty DB
make up                     # hub + the arena eval image (needed for `evolve`)
make hub-reset              # wipe the DB and start over
```

Each worktree gets its own port, compose project, and arena image tag, so
parallel checkouts don't collide. The mutator image is deliberately shared. Full
details, including the ordering gotcha on the very first `make up`:
[`local-stack.md`](local-stack.md).

To point the CLI at production from inside a worktree, use `nethackers --prod`.

## Two traps that cost real time

**Sandbox image pins.** `src/nethackers/_image_pins.py` pins the images a release
pulls. How each one changes:

- **The mutator is automatic.** A PR that touches `Dockerfile.mutator`, the
  entrypoint, or the package code the mutator copies (`src/nethackers/__init__.py`,
  `arena/`, `contracts/`) gets its image built, pushed and re-pinned by
  `.github/workflows/mutator-image.yml`, which commits the new pin to your branch.
  Wait for that commit before merging. A fork PR's image is published by the run on
  `main` after merge. The release refuses to publish if the mutator pin doesn't
  match the tagged files.
- **The arena and the shared base are manual.** CI fails if `arena/Dockerfile`,
  `nle-base/Dockerfile`, `uv.lock`, `src/nethackers/arena/` or
  `src/nethackers/contracts/` changed since the last release tag without
  `ARENA_IMAGE` changing. Run `.github/workflows/sandbox-images.yml` on your branch,
  then classify the new arena digest in `src/nethackers/arena_version.py`: a
  rebuild that doesn't move scores keeps the verified corpus, and one that does
  bumps `ARENA_MAJOR`, which retires that corpus from every board (nothing is
  deleted). The diff base is **the last `v*` release tag, not your PR's base
  branch**, so this can fire for someone else's unreleased merge. Touching
  `uv.lock` trips it, so a routine dependency bump is not routine here.

**Injectable seams bind at import.** Many functions take dependencies as keyword
defaults (`run=subprocess.run`, `repo_root=...`). Those defaults are evaluated at
import time, so `monkeypatch.setattr` on the module attribute is a **silent
no-op** — your test passes while testing nothing. Inject explicitly, or patch the
name that is actually dereferenced at call time.

## Pull requests

- Branch off `main`; PRs target `main`.
- Keep the diff scoped to one change. CI must be green.
- Changes with a design behind them say so in the PR description: what you
  considered, what you chose, and why. Reference material is the top-level
  docs in this directory — keep it current in the same PR.
- If you change user-facing behavior, update the docs in the same PR.

## Releasing

Two workflows, **one shared tag** — they are not independent, which is the part
that catches people out.

1. Bump the version in `pyproject.toml` and merge to `main` **first**. The PyPI
   workflow asserts the tag matches the version, and PyPI rejects a re-upload, so
   a wrong version number burns one.
2. `git tag vX.Y.Z && git push origin vX.Y.Z` → builds the hub image and flips
   production by digest, health-checked, with automatic rollback. Opt out with
   `[skip hub-deploy]` in the tagged commit message. See
   [`../deploy/README.md`](../deploy/README.md).
3. `gh release create vX.Y.Z` → publishes to PyPI. Publishing is *release*-
   triggered, not tag-triggered. It also blocks until both pinned sandbox image
   digests exist in GHCR (up to 20 min), so re-pin before releasing.

The trap: if the tag does not exist yet, `gh release create` **creates and pushes
it**, which fires the hub deploy too. If you meant to publish only to PyPI, tag
deliberately in step 2 with `[skip hub-deploy]` rather than letting step 3 do it
for you.

## Reporting a problem

`nethackers report` prints the most recent local crash report. It is read-only
and offline — it sends nothing anywhere, and the project ships no telemetry or
analytics. Paste it into an issue yourself if you'd like us to look.

Include `nethackers doctor -o json` for anything environment-shaped; see
[`troubleshooting.md`](troubleshooting.md) first, which covers the common cases.
