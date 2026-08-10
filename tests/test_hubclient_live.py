from rich.console import Console

from nethackers.hubclient.live import EpisodeStream, episode_table


class _FakeConsole:
    def __init__(self) -> None:
        self.printed: list = []

    def print(self, renderable) -> None:
        self.printed.append(renderable)


class _FakeLive:
    def __init__(self) -> None:
        self.console = _FakeConsole()
        self.updates: list = []

    def update(self, renderable, refresh: bool = False) -> None:
        self.updates.append(renderable)


def _ep(index, total, seed, *, char="val-dwa-law-fem", progress=0.1,
        status="completed", turns=100, depth=1) -> dict:
    return {
        "index": index, "total": total, "seed": seed, "character": char,
        "progress": progress, "status": status, "turns": turns, "depth": depth,
    }


def test_new_batch_freezes_previous_to_scrollback():
    live = _FakeLive()
    stream = EpisodeStream(live)
    stream.on_episode("cold-start · dev", _ep(1, 2, 0))
    stream.on_episode("cold-start · dev", _ep(2, 2, 1))
    assert live.console.printed == []  # batch still filling -> nothing frozen yet

    stream.on_episode("cold-start · held-out", _ep(1, 2, 1000))  # label change
    assert len(live.console.printed) == 1  # the dev batch frozen exactly once

    stream.finish()
    assert len(live.console.printed) == 2  # the held-out batch frozen at the end


def test_updates_live_region_once_per_episode_within_a_batch():
    live = _FakeLive()
    stream = EpisodeStream(live)
    stream.on_episode("iter 1/3 · dev", _ep(1, 8, 0))
    stream.on_episode("iter 1/3 · dev", _ep(2, 8, 1))
    # Two episodes, same batch, no freeze -> two live updates, no scrollback.
    assert len(live.updates) == 2
    assert live.console.printed == []


def test_finish_without_any_episode_is_a_noop():
    live = _FakeLive()
    EpisodeStream(live).finish()
    assert live.console.printed == [] and live.updates == []


def test_episode_table_renders_seed_status_and_turns(capsys):
    rows = [_ep(1, 2, 42, status="ascended", progress=1.0, turns=1234, depth=30)]
    Console(force_terminal=False, width=120).print(
        episode_table("iter 1/3 · dev", rows, done=True)
    )
    out = capsys.readouterr().out
    assert "iter 1/3 · dev" in out
    assert "42" in out and "ascended" in out
    assert "1,234" in out  # turns are thousands-grouped
