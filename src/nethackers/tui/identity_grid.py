"""The evolve-form's objective picker: a Frontier-style grid of role cards
where every variation is a selection box. Pure helpers (``nav_order``/
``selection_token``/``render_identity_grid``) live here so they unit-test
without a mount; the interactive widget (``IdentityGrid``) wraps them.

Mirrors ``hubclient.render.render_frontier_grid``'s role-card layout (same
``ROLE_FULL``/``ROLE_ORDER`` grouping, all 73 ``IDENTITIES``) so the picker
reads like the Frontier the user already knows -- but each cell is a
``◻``/``◼`` box, role headers carry a live ``selected/total`` count, and a
gold cursor marks the focused cell."""
from __future__ import annotations

from collections import defaultdict

from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text
from textual import events
from textual.message import Message
from textual.widgets import Static

from nethackers.hub.objectives import IDENTITIES
from nethackers.hubclient.render import ROLE_FULL, ROLE_ORDER

# role -> its identities (sorted), computed once from the catalog.
_BY_ROLE: dict[str, list[str]] = defaultdict(list)
for _ident in IDENTITIES:
    _BY_ROLE[_ident.split("-", 1)[0]].append(_ident)
for _role in _BY_ROLE:
    _BY_ROLE[_role].sort()

_BOX = {True: "◼", False: "◻"}
_CURSOR = "black on #ffd54a"      # the focused cell: gold chip (matches the app cursor)
_HEAD = "bold #d2a24c"            # role name, in the shell's gold
_LABEL = "#8a8069"               # a variation's race-align-gender label
_ON = "#d7c9a2"                  # a selected box + label
_OFF = "#5a5a52"                 # an unselected box (dim)


def identities_of(role: str) -> list[str]:
    """The role's variations (identity strings), sorted. ``[]`` for an unknown role."""
    return list(_BY_ROLE.get(role, []))


def nav_order() -> list[str]:
    """The flat cursor order over every navigable cell, in reading order: for
    each role its header token ``"role:<role>"`` followed by its identity
    cells. Left/right hop between the ``role:`` anchors; up/down step one cell."""
    order: list[str] = []
    for role in ROLE_ORDER:
        order.append(f"role:{role}")
        order.extend(_BY_ROLE[role])
    return order


def anchors() -> list[str]:
    """The left/right hop targets: every ``"role:<role>"`` header."""
    return [f"role:{role}" for role in ROLE_ORDER]


def selection_token(selected: set[str]) -> str:
    """Render the picked set to a token ``selector.resolve`` accepts: ``""``
    when nothing is picked; ``"*"`` when all 73 are picked; the bare role when
    exactly one full role (and nothing else) is picked; else a sorted, deduped
    comma-list of the picked identities."""
    if not selected:
        return ""
    if selected == set(IDENTITIES):
        return "*"
    roles = {i.split("-", 1)[0] for i in selected}
    if len(roles) == 1:
        (role,) = roles
        if selected == set(_BY_ROLE[role]):
            return role
    return ",".join(sorted(selected))


def _role_count(role: str, selected: set[str]) -> tuple[int, int]:
    idents = _BY_ROLE[role]
    return sum(1 for i in idents if i in selected), len(idents)


def _cell(token: str, text: str, style: str, cursor: str) -> Text:
    """One rendered line -- gold-chipped when it's the cursor cell."""
    return Text(text, style=_CURSOR if token == cursor else style)


def render_identity_grid(
    selected: set[str], cursor: str, *, ncols: int = 3
) -> RenderableType:
    """The picker as an ``ncols``-column grid of role cards. Each card:
    ``<Role>  n/total`` header, then one ``◻``/``◼`` box per variation
    (``race-align-gender``). The cursor cell is gold-chipped."""
    cards: list[Text] = []
    for role in ROLE_ORDER:
        n, tot = _role_count(role, selected)
        head_token = f"role:{role}"
        card = Text()
        card.append_text(_cell(head_token, ROLE_FULL[role], _HEAD, cursor))
        card.append(f"  {n}/{tot}\n", style="dim" if head_token != cursor else _CURSOR)
        for ident in _BY_ROLE[role]:
            on = ident in selected
            var = ident.split("-", 1)[1]
            if ident == cursor:
                card.append(f"{_BOX[on]} {var}\n", style=_CURSOR)
            else:
                card.append(_BOX[on] + " ", style=_ON if on else _OFF)
                card.append(f"{var}\n", style=_ON if on else _LABEL)
        cards.append(card)

    table = Table(box=None, show_header=False, show_edge=False,
                  pad_edge=False, padding=(0, 1), collapse_padding=True, expand=True)
    for _ in range(ncols):
        table.add_column(ratio=1)
    for i in range(0, len(cards), ncols):
        row = cards[i:i + ncols]
        row += [Text("")] * (ncols - len(row))
        table.add_row(*row)

    summary = Text(f"\n{len(selected)} build(s) selected", style="dim")
    return Group(table, summary)


def _anchor_of(cursor: str) -> str:
    """The left/right hop anchor the cursor sits under: itself if it is a
    ``role:`` header, else its role's header."""
    if cursor.startswith("role:"):
        return cursor
    return f"role:{cursor.split('-', 1)[0]}"


class IdentityGrid(Static):
    """The interactive objective picker: a focusable Frontier-style grid whose
    cells are selection boxes. Arrows move the cursor (up/down one cell,
    left/right hop roles), space toggles it (an identity or a whole role via its
    header), ``a`` selects all 73, ``c`` clears all. Posts ``Changed`` on every
    selection change; ``token()`` renders the picked set to a
    ``selector.resolve`` string. Escape is left to bubble so the form's modal
    nav reclaims control."""

    can_focus = True

    class Changed(Message):
        def __init__(self, grid: IdentityGrid) -> None:
            self.grid = grid
            super().__init__()

    def __init__(self, **kwargs: object) -> None:
        super().__init__("", **kwargs)
        self.selected: set[str] = set()
        self._order = nav_order()
        self._anchors = anchors()
        self.cursor: str = self._order[0]  # the first role header

    def on_mount(self) -> None:
        self._repaint()

    # ---- read ---------------------------------------------------------------
    def token(self) -> str:
        return selection_token(self.selected)

    # ---- render -------------------------------------------------------------
    # NB: not ``_render`` -- that name is Textual Widget's own internal, which
    # must return a visual; overriding it with a None-returning repaint crashes
    # layout.
    def _repaint(self) -> None:
        self.update(render_identity_grid(self.selected, self.cursor))

    def _changed(self) -> None:
        self._repaint()
        self.post_message(self.Changed(self))

    # ---- cursor -------------------------------------------------------------
    def _move(self, delta: int) -> None:
        idx = self._order.index(self.cursor)
        idx = max(0, min(len(self._order) - 1, idx + delta))
        self.cursor = self._order[idx]
        self._repaint()

    def _hop(self, delta: int) -> None:
        anchor = _anchor_of(self.cursor)
        idx = self._anchors.index(anchor)
        idx = max(0, min(len(self._anchors) - 1, idx + delta))
        self.cursor = self._anchors[idx]
        self._repaint()

    # ---- selection ----------------------------------------------------------
    def _toggle(self) -> None:
        if self.cursor.startswith("role:"):
            idents = set(identities_of(self.cursor.split(":", 1)[1]))
            self.selected = (self.selected - idents
                             if idents <= self.selected else self.selected | idents)
        elif self.cursor in self.selected:
            self.selected.discard(self.cursor)
        else:
            self.selected.add(self.cursor)
        self._changed()

    def select_all(self) -> None:
        self.selected = set(IDENTITIES)
        self._changed()

    def clear_all(self) -> None:
        self.selected = set()
        self._changed()

    def on_key(self, event: events.Key) -> None:
        handlers = {
            "down": lambda: self._move(1), "up": lambda: self._move(-1),
            "right": lambda: self._hop(1), "left": lambda: self._hop(-1),
            "space": self._toggle, "a": self.select_all, "c": self.clear_all,
        }
        action = handlers.get(event.key)
        if action is not None:
            action()
            event.stop()  # escape is NOT handled here -> bubbles to the form's modal nav
