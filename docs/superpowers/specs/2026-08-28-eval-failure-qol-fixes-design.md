# Eval-failure QoL fixes — legible, non-crashing run failures

- **Date:** 2026-08-28
- **Status:** Approved design (store-race approach = atomic rename, chosen by user)
- **Scope:** 5 fixes across 4 files. No success-path behavior change, no new deps.

## Motivation

A mundane environment problem — the `nethackers/arena:dev` image not being
built locally, so `docker run …` exits **125** — cascaded into a hard TUI
crash and an *undiagnosable* error, and neighbouring weaknesses added a
parallel-run crash and an intermittent monitor crash. Diagnosis this session
(two screenshots + `log.txt`) traced five **independent** weaknesses in how a
failed run is *reported* and how shared state is *written*:

1. The failure toast crashes the whole app on any error containing `[`.
2. Docker's stderr (the actual reason for 125) is thrown away.
3. The toast message is the ~3,800-char command repr.
4. The content-addressed store races on concurrent writes → `FileExistsError`.
5. Monitor backfill queries a tab pane before it mounts → `NoMatches`.

Key evidence: the `MarkupError` was raised in the compositor's **reflow path**
(uncatchable, so it kills the app) from
`textual/widgets/_toast.py:115 → Content.from_markup(notification.message)`,
because `notify()` left `markup=True` on a bracket-filled message.

These fixes make failures **survivable, legible, and self-explaining**. They do
**not** touch the install / arena-image preflight story (deferred by the user).

**Non-goals:** arena-image preflight/build UX; a broader notification framework;
any refactor beyond the touched functions.

## Fixes

### Fix 1 + 3 — `app.py`: non-crashing, short failure toast

**File:** `src/nethackers/tui/app.py` (`_finish_run`, ~line 327)

Change the failure toast:

```python
# before
self.notify(f"run {run.rid} failed: {error}", severity="error", timeout=10)
# after
self.notify(f"run {run.rid} failed: {failure_detail(error)}",
            severity="error", timeout=10, markup=False)
```

Add `import subprocess` and a module-level helper + constant:

```python
_TOAST_DETAIL_MAXLEN = 200  # a toast shows a one-line reason; full error lives in the run/log

def failure_detail(error: BaseException) -> str:
    """One-line, human reason for a failed run, safe for a plain-text toast.

    Prefers the tail of a captured subprocess stderr (e.g. Docker's own error
    line), else a compact exit-status line, else the exception text — never the
    giant CalledProcessError command repr."""
    stderr = getattr(error, "stderr", None)
    if stderr:
        text = stderr.decode() if isinstance(stderr, bytes) else str(stderr)
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if lines:
            return _cap(lines[-1])
    if isinstance(error, subprocess.CalledProcessError):
        return _cap(f"eval exited {error.returncode}")
    return _cap(str(error))

def _cap(s: str) -> str:
    s = s.strip()
    return s if len(s) <= _TOAST_DETAIL_MAXLEN else s[: _TOAST_DETAIL_MAXLEN - 1] + "…"
```

**Why `markup=False`:** the message can contain `[` (paths, reprs). With
`markup=True`, Textual runs it through `Content.from_markup` during toast layout
(inside the compositor reflow), and a stray bracket raises `MarkupError` that
escapes the render loop and kills the app. `markup=False` takes the plain
`Content(message)` branch. Leave the other `self.notify(...)` calls
(`monitor.py:144/415/417`) as-is — static strings, no interpolation.

**Tests** (`tests/test_tui_app.py`):
- `failure_detail`: (a) error with multi-line `.stderr` → last non-empty line;
  (b) `CalledProcessError` w/o stderr → `"eval exited N"` (not the command repr);
  (c) overlong → capped with `…`; (d) plain `Exception` → its text.
- `_finish_run` passes `markup=False`: subclass/fake `App` capturing `notify`
  kwargs (or monkeypatch `App.notify`); assert `markup is False` and a bounded
  message length. Follow existing `test_tui_app.py` patterns.

### Fix 2 — `runner.py`: attach Docker's stderr to the eval error

**File:** `src/nethackers/eval/runner.py` (`_stream_episodes`)

Add `from collections import deque` (top) and a constant:

```python
_STDERR_TAIL_LINES = 40  # a docker/arena failure is a handful of lines; keep enough
                         # for context, bounded so a chatty run can't grow memory
```

Capture a rolling tail and attach it on non-zero exit:

```python
proc = popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, bufsize=1)
tail: deque[str] = deque(maxlen=_STDERR_TAIL_LINES)
for line in proc.stderr:
    tail.append(line)
    m = _ARENA_EPISODE.search(line)
    if m is None:
        continue
    ...  # unchanged episode-parsing / on_episode(...)
returncode = proc.wait()
if returncode != 0:
    raise subprocess.CalledProcessError(returncode, cmd, stderr="".join(tail))
```

Leave the **non-streaming** path (`runner(cmd, check=True)`, ~line 177, used when
`on_episode is None` — baseline/CLI) unchanged: it already lets Docker's stderr
reach the terminal, and capturing it would suppress live progress there.

**Test** (`tests/test_eval_runner_m2a.py`): mirror
`test_eval_batch_streams_per_episode_when_on_episode_given`'s `fake_popen`; have
it yield a couple of non-episode stderr lines including a docker-style error and
return `returncode=125`; assert `eval_batch(..., on_episode=...)` raises
`CalledProcessError` whose `.stderr` contains the docker line and `.returncode == 125`.

### Fix 4 — `store.py`: atomic, race-safe save

**File:** `src/nethackers/harness/store.py`

Add `import os` and `from uuid import uuid4`. Publish atomically via a temp dir
+ rename:

```python
def save(self, src):
    src = Path(src)
    digest = _solution_digest(src)
    self._publish(src, self.path(digest))
    return digest

def save_as(self, digest, src):
    self._publish(Path(src), self.path(digest))

def _publish(self, src: Path, dest: Path) -> None:
    """Copy src -> dest atomically. Content-addressed: if dest already exists
    (or a concurrent writer wins the race) it's the same tree, so leave it.
    Readers never see a partially-copied dest."""
    if dest.is_dir():
        return
    tmp = self._root / f".tmp-{uuid4().hex}"
    shutil.copytree(src, tmp)
    try:
        os.rename(tmp, dest)          # atomic publish (tmp is a sibling → same filesystem)
    except OSError:                   # lost the race; dest now present, identical content
        shutil.rmtree(tmp, ignore_errors=True)
```

`.tmp-*` names never collide with a digest key (`_key` never yields a `.tmp-`
prefix), so `path`/`has` lookups are unaffected. A crash mid-copy can leave a
`.tmp-*` dir — harmless and rare; reaping is out of scope.

**Post-condition invariant** (assert in tests): after `save`/`save_as` returns
without raising, `self.path(digest).is_dir()` holds.

**Tests** (`tests/test_harness_store.py`):
- `save` twice on the same tree → no raise, identical digest, dest complete.
- Pre-create `dest` (concurrent winner already published) then `save` → returns
  digest, no raise, dest untouched, no `.tmp-*` left.
- Race faithfully: monkeypatch `os.rename` to **create `dest` and then raise
  `OSError`** (a concurrent winner appearing between the `is_dir` check and the
  rename) → `_publish` swallows it, `dest` is present, no `.tmp-*` left.
- `save_as` idempotent when dest already exists.

### Fix 5 — `monitor.py`: guard backfill against an unmounted pane

**File:** `src/nethackers/tui/screens/monitor.py` (`_backfill`; `NoMatches`
already imported, used with `except` at lines 260/278/337)

Make the pane query the **first** statement; on `NoMatches`, reschedule and
return before anything mutates:

```python
def _backfill(self) -> None:
    try:
        scroll = self.query_one("#tables", VerticalScroll)
    except NoMatches:
        # TabbedContent hasn't mounted its pane content yet; one
        # call_after_refresh isn't a firm guarantee. Retry next frame.
        # Guaranteed to terminate: #tables is unconditional in compose().
        self.call_after_refresh(self._backfill)
        return
    for tag in self.run.logs:
        self._ensure_log_item(tag)
    for batch in self.run.batches:
        static = Static()
        scroll.mount(static)
        self._batch_statics.append(static)
        static.update(episode_table(batch.label, batch.rows(), done=batch.done))
    if self.run.sel_tag:
        self._select_log(self.run.sel_tag)
    self.call_after_refresh(self._highlight_current)
    self.render_state()
```

Because the guard precedes every mutation, a retry re-runs nothing (no
double-mounted tables); the loops run exactly once.

**Test** (`tests/test_tui_monitor.py`, follow the existing NoMatches-crash test
near line 153): opening a `RunMonitor` whose run has backfill (logs/batches)
before the pane mounts does **not** raise and eventually renders the tables — or
unit-level: monkeypatch `query_one` to raise `NoMatches` once then return a fake
scroll; assert `_backfill` reschedules (`call_after_refresh` called) and
completes on the second call.

## Dependencies, integration & verification

- **Disjoint files** (`app.py`, `runner.py`, `store.py`, `monitor.py`) → the four
  work-units are independent and parallelizable.
- Fix 3's helper reads `error.stderr` that Fix 2 populates, but degrades
  gracefully when absent → **no code dependency** between A and B.
- **Integration outcome:** a failed eval shows a short toast (e.g.
  `run … failed: docker: Unable to find image 'nethackers/arena:dev'`) instead of
  crashing; parallel runs don't crash the store; opening a monitor mid-backfill
  doesn't crash.
- **Tests:** each work-unit ships its own unit test(s); all use fakes/`tmp_path`
  — **no Docker, no cluster** required. `make test` (or pytest on the touched
  test files) green before hand-back.
- **Rollout:** single branch; held for the user's manual test per the
  manual-test gate before any push/PR.

## Work-unit → subagent map

| Unit | File | Fixes | Test file |
|------|------|-------|-----------|
| A | `src/nethackers/tui/app.py` | 1 + 3 | `tests/test_tui_app.py` |
| B | `src/nethackers/eval/runner.py` | 2 | `tests/test_eval_runner_m2a.py` |
| C | `src/nethackers/harness/store.py` | 4 | `tests/test_harness_store.py` |
| D | `src/nethackers/tui/screens/monitor.py` | 5 | `tests/test_tui_monitor.py` |
