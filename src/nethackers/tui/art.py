"""Pure NetHack set-pieces: score→Dlvl derivation, the RIP tombstone, and the
high-score board table. No Textual; unit-tested as strings/Tables."""
from __future__ import annotations

from rich import box
from rich.table import Table
from rich.text import Text

from nethackers.arena.progress import ACHIEVEMENTS

_DLVL = sorted(((k, v) for k, v in ACHIEVEMENTS.items() if k.startswith("Dlvl:")),
               key=lambda kv: kv[1])
_ASTRAL = ACHIEVEMENTS["Astral Plane"]


def score_to_dlvl(score: float) -> str:
    """Nearest dungeon level at or below a 0–1 progression score."""
    if score >= _ASTRAL:
        return "Astral"
    best = _DLVL[0][0]
    for name, val in _DLVL:
        if val <= score:
            best = name
        else:
            break
    return best


def _titlecase_identity(identity: str) -> str:
    return "-".join(p.capitalize() for p in identity.split("-")) if identity else "—"


def tombstone(lines: list[str]) -> str:
    """The classic NetHack RIP headstone with up to 4 centered epitaph lines."""
    inner = 20
    rows = ["RIP".center(inner), "".center(inner)]
    for ln in lines[:4]:
        rows.append(ln[:inner].center(inner))
    while len(rows) < 7:
        rows.append("".center(inner))
    out = ["     " + "_" * (inner - 4)]
    out.append("    /" + " " * (inner - 3) + "\\")
    for r in rows:
        out.append("   | " + r + " |")
    out.append("   |" + "_" * (inner + 2) + "|")
    return "\n".join(out)


def highscore_table(entries: list[dict], you: str | None = None) -> Table:
    """Board rendered in NetHack's high-score idiom. Owner-keyed when rows carry
    ``owner`` (ranking boards); identity-keyed otherwise (elites)."""
    t = Table(box=box.SIMPLE_HEAVY, header_style="bold", pad_edge=False)
    t.add_column("No", justify="right")
    t.add_column("Points", justify="right")
    t.add_column("Dlvl", justify="right")
    t.add_column("Who")
    for e in entries:
        owner = str(e.get("owner", ""))
        score = float(e.get("mean_progression", e.get("score", 0.0)) or 0.0)
        is_you = you is not None and owner != "" and owner == you
        style = "bold reverse" if is_you else ""
        who = f"@{owner}" if owner else _titlecase_identity(str(e.get("identity", "")))
        if is_you:
            who += " ◀ you"
        t.add_row(str(e.get("rank", "")), f"{score:.3f}", score_to_dlvl(score),
                  Text(who, style=style))
    return t
