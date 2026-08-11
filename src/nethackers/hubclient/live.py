"""Live, in-place rendering of the arena's per-episode stream for ``evolve``.

``run_loop`` calls ``on_episode(label, ep)`` once per finished arena episode,
where ``label`` names the batch it belongs to (``"cold-start · dev"``,
``"iter 1/3 · held-out"``, …) and ``ep`` is the parsed
``{seed, character, progress, status, turns, depth, index, total}`` dict.

``EpisodeStream`` turns that stream into one table per batch that fills in
row-by-row *in place*; when a batch finishes (the next one starts, or the run
ends) its table is frozen into scrollback and the next batch starts fresh.
Seeds are the left column so same-seed rows line up across batches by eye
(e.g. ``cold-start · dev`` seed 3 vs ``iter 1/3 · dev`` seed 3).
"""
from __future__ import annotations

from rich import box
from rich.live import Live
from rich.table import Table
from rich.text import Text

_STATUS_STYLE = {
    "ascended": "bold green",
    "completed": "green",
    "died": "yellow",
    "aborted": "yellow",
    "error": "red",
}


def _progress_style(progress: float) -> str:
    if progress >= 0.5:
        return "bold green"
    if progress >= 0.1:
        return "green"
    if progress > 0.0:
        return "yellow"
    return "dim"


def episode_table(label: str, rows: list[dict], *, done: bool) -> Table:
    """A batch's table: one row per finished episode, in batch (seed) order.

    Styled cells are ``Text`` objects (not markup strings) so they render
    correctly even when a ``markup=False`` ``report`` print triggers the live
    re-render -- embedded ``[style]`` markup would otherwise show literally.
    """
    total = rows[-1]["total"] if rows else 0
    table = Table(
        title=label,
        title_justify="left",
        title_style="bold",
        caption="✓ complete" if done else f"running… {len(rows)}/{total}",
        caption_justify="right",
        caption_style="green" if done else "dim",
        box=box.ROUNDED,
        header_style="dim",
        pad_edge=False,
    )
    table.add_column("seed", justify="right")
    table.add_column("character")
    table.add_column("progress", justify="right")
    table.add_column("status")
    table.add_column("turns", justify="right")
    table.add_column("depth", justify="right")
    for ep in rows:
        status = str(ep["status"])
        progress = float(ep["progress"])
        table.add_row(
            str(ep["seed"]),
            str(ep["character"]),
            Text(f"{progress:.3f}", style=_progress_style(progress)),
            Text(status, style=_STATUS_STYLE.get(status, "")),
            f"{int(ep['turns']):,}",
            str(ep["depth"]),
        )
    return table


class EpisodeStream:
    """Feed ``on_episode`` into a ``rich.live.Live``: one in-place table per
    batch, each frozen to scrollback when the next batch starts. Call
    ``finish()`` once at the end to freeze the final batch."""

    def __init__(self, live: Live) -> None:
        self._live = live
        self._label: str | None = None
        self._rows: list[dict] = []

    def on_episode(self, label: str, ep: dict) -> None:
        if label != self._label:
            self._freeze()
            self._label = label
            self._rows = []
        self._rows.append(ep)
        self._live.update(episode_table(label, self._rows, done=False), refresh=True)

    def finish(self) -> None:
        self._freeze()
        self._label = None
        self._rows = []

    def _freeze(self) -> None:
        # Print the finished batch above the live region, then clear the region
        # so the next batch's table doesn't render beneath a stale copy.
        if self._label is not None and self._rows:
            self._live.console.print(episode_table(self._label, self._rows, done=True))
            self._live.update(Text(""), refresh=True)
