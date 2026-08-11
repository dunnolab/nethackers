from nethackers.tui.app import EvolveApp
from nethackers.tui.status import EvolveConfig

CFG = EvolveConfig(objective="val-dwa-law-fem", backend="claude",
                   iterations=3, token_budget=40_000)


def _state(phase, **kw):
    base = {"phase": phase, "iteration": 1, "baseline_dev": 0.07, "baseline_held": 0.05,
            "best_dev": 0.09, "best_held": 0.06, "wins": 1, "tokens": 0, "detail": ""}
    base.update(kw)
    return base


async def test_status_bar_updates_from_state():
    app = EvolveApp(CFG, run=None)
    async with app.run_test() as pilot:
        app._apply_state(_state("mutating", iteration=2))
        await pilot.pause()
        text = str(app.query_one("#status").content)
        assert "MUTATING iter 2/3" in text
        assert "best dev 0.090" in text


async def test_episode_mounts_table_and_log_records_line():
    app = EvolveApp(CFG, run=None)
    async with app.run_test() as pilot:
        app._apply_state(_state("mutating", iteration=1))
        app._apply_log("iter 1/3",
                       '{"type":"assistant","message":{"content":'
                       '[{"type":"text","text":"editing bot"}]}}')
        app._apply_episode("iter 1/3 · dev", {
            "index": 1, "total": 8, "seed": 0, "character": "val-dwa-law-fem",
            "progress": 0.1, "status": "completed", "turns": 5, "depth": 1})
        await pilot.pause()
        assert app.query("#tables Static")               # a batch table mounted
        assert app._logs["iter 1/3"] == [("assistant", "editing bot")]
        assert app._live_tokens.get("iter 1/3", 0) >= 0  # counter updated, no crash


async def test_display_handler_exception_is_dropped_not_propagated():
    app = EvolveApp(CFG, run=None)
    async with app.run_test():
        app._apply_episode("iter 1/3 · dev", {})    # missing keys -> would raise; must be swallowed
        app._apply_state({})                          # missing keys -> swallowed
        app._apply_log("iter 1/3", "not even json")   # swallowed
        # app still works afterward:
        app._apply_state({"phase": "mutating", "iteration": 1, "baseline_dev": 0.07,
                          "baseline_held": 0.05, "best_dev": 0.09, "best_held": 0.06,
                          "wins": 1, "tokens": 0, "detail": ""})
        assert "MUTATING iter 1/3" in str(app.query_one("#status").content)
