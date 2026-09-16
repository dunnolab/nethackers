"""Locating and validating the directory a run is scored from.

Two jobs, both about the solution root a caller named.

**Resolution.** ``roots/autoascend`` is how the README and four other docs name
the reference solution, and it resolves against the CWD -- which only works
inside a repo checkout. A ``pip install nethackers`` user has no checkout, so
the wheel carries its own copy of the tree (force-included at
``nethackers/roots/autoascend``; see pyproject) and AutoAscend's canonical
names resolve to it when nothing is on disk. A path that DOES exist always
wins, so a checkout is unaffected and a user's own ``roots/autoascend`` is
never shadowed by ours. Only AutoAscend's own names get this fallback: mapping
any missing path onto a tree the caller did not ask for would silently score
the wrong solution.

**Validation.** A missing root used to be invisible. Docker's bind mount
CREATES an absent host directory, so every episode ran against an empty folder,
died inside the sandbox with "submission must contain bot.py", and the batch
still reported a clean ``mean_progress`` of 0.0 at exit code 0 -- a
real-looking score for a solution that was never there, from any typo'd path.
``eval_batch`` calls ``require_solution_root`` before it mounts anything, so no
scoring path can produce that number again.

Stdlib-only leaf (``hub.ids`` is itself stdlib-only), so the CLI, the TUI, the
hub's baseline compute and the verifier worker can all import it.
"""
from __future__ import annotations

from pathlib import Path

from nethackers.hub.ids import AUTOASCEND_TREE

#: The wheel's own copy of the vendored solution roots. Deliberately absent in
#: a repo checkout -- there the trees live at the repo root, outside the
#: package, and the relative path resolves on its own.
PACKAGED_ROOTS = Path(__file__).parent / "roots"

#: The file the arena loads a bot from (``arena.sandbox._load_agent``), and so
#: the one thing that makes a directory a solution root.
ENTRYPOINT = "bot.py"

#: Spellings of the reference solution that earn the fallback: the documented
#: path, and the bare name it is easier to type.
_AUTOASCEND_NAMES = frozenset({AUTOASCEND_TREE, Path(AUTOASCEND_TREE).name})


class SolutionRootError(ValueError):
    """The named solution root can't be scored.

    Carries a finished, user-facing sentence: ``cli.main`` prints ``str(exc)``
    as an ordinary error, never as the red "unexpected error" banner (which
    would also write a crash report for what is plainly a typo).
    """


def resolve_solution_root(spec: str | Path) -> Path:
    """The directory ``spec`` names, with AutoAscend's canonical names falling
    back to a real tree when the literal path isn't there.

    An existing path is returned verbatim. Otherwise, for AutoAscend's names
    only, the checkout location is tried before the packaged copy -- if both
    are present (a pip install inside a checkout) the developer's own tree is
    the one they mean. An unresolvable path comes back unchanged so the error
    names what the caller actually typed.
    """
    path = Path(spec)
    if path.exists():
        return path
    if path.as_posix() in _AUTOASCEND_NAMES:
        for candidate in (Path(AUTOASCEND_TREE), PACKAGED_ROOTS / Path(AUTOASCEND_TREE).name):
            if candidate.is_dir():
                return candidate
    return path


def check_solution_root(path: Path) -> str | None:
    """``None`` if ``path`` can be scored, else one plain sentence saying why.

    Plain text, not Rich markup: ``eval_batch`` raises it and the worker logs
    it, neither of which renders markup.
    """
    if not path.exists():
        return f"no solution at '{path}' — {_shape()}. {_hint()}"
    if not path.is_dir():
        return f"'{path}' is a file, not a solution root — {_shape()}. {_hint()}"
    if not (path / ENTRYPOINT).is_file():
        return f"'{path}' has no {ENTRYPOINT} — {_shape()}. {_hint()}"
    return None


def require_solution_root(spec: str | Path) -> Path:
    """``resolve_solution_root`` + ``check_solution_root``, raising
    ``SolutionRootError`` rather than returning a path that cannot be scored."""
    path = resolve_solution_root(spec)
    problem = check_solution_root(path)
    if problem is not None:
        raise SolutionRootError(problem)
    return path


def _shape() -> str:
    return f"a solution root is a directory containing {ENTRYPOINT}"


def _hint() -> str:
    """Where to get AutoAscend, answered for the caller's actual install."""
    if (PACKAGED_ROOTS / Path(AUTOASCEND_TREE).name).is_dir():
        return "nethackers ships AutoAscend: pass 'autoascend' to start from it."
    return f"AutoAscend ships with nethackers as '{AUTOASCEND_TREE}'."
