"""A DataTable whose single highlight (the cursor) follows the mouse but ONLY
onto clickable cells (per an injected ``_valid_fn``); the table's own hover
highlight is disabled entirely, so headers / group rows / static columns never
light up. A single click selects. Plus a module-level Textual 8.2.x guard: a
full-region render can hand ``Visual.to_strips`` a ``None`` background visual
for a widget's own (child-covered) layer and crash -- render it blank instead."""
from __future__ import annotations

from collections.abc import Callable

import textual.visual as _tv
from textual import events
from textual.coordinate import Coordinate
from textual.strip import Strip
from textual.widgets import DataTable

_ORIG_TO_STRIPS = _tv.Visual.to_strips


def _guarded_to_strips(widget, visual, width, height, style, *args, **kwargs):
    if visual is None:
        return [Strip.blank(width)] * (height or 1)
    return _ORIG_TO_STRIPS(widget, visual, width, height, style, *args, **kwargs)


_tv.Visual.to_strips = staticmethod(_guarded_to_strips)  # type: ignore[method-assign]


class ClickTable(DataTable):
    _valid_fn: Callable[[int, int], bool] | None = None

    def _set_hover_cursor(self, active: bool) -> None:
        super()._set_hover_cursor(False)          # kill the separate hover highlight

    def _on_mouse_move(self, event: events.MouseMove) -> None:
        meta = event.style.meta
        if meta and "row" in meta and "column" in meta:
            row, col = meta["row"], meta["column"]
            if self._valid_fn is not None and self._valid_fn(row, col):
                self.cursor_coordinate = Coordinate(row, col)
        # non-clickable cell / empty meta: leave the highlight where it is

    async def _on_click(self, event: events.Click) -> None:
        meta = event.style.meta
        if "row" not in meta or "column" not in meta:
            return
        row_index, column_index = meta["row"], meta["column"]
        if row_index < 0 or column_index < 0:
            return
        self.cursor_coordinate = Coordinate(row_index, column_index)
        self._post_selected_message()
        self._scroll_cursor_into_view(animate=False)
        event.stop()
