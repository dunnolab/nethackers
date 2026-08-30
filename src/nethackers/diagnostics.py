"""Diagnostics: the version regimes ``nethackers`` reports about itself
(spec ``docs/superpowers/specs/2026-08-28-sandbox-image-distribution-design.md``
5.7/5.6). ``version_info()`` backs the top-level ``--version`` flag (cli.py)
and is also the single builder the future ``doctor -o json``'s ``env``
header extends (Plan 3 Task 2) -- INV5's "one source of truth" for what a
build IS, as opposed to what's actually installed/reachable on this machine
(``doctor``'s job) or what actually ran on a given evolve run (the per-run
provenance record, ``harness/runlog.py``).

Pure and offline (INV7: version reports *pins*, never pulled/live state) --
no Docker call, no network call, no filesystem read beyond the installed
package's own metadata."""
from __future__ import annotations

from importlib.metadata import version as _pkg_version

from nethackers import _image_pins
from nethackers.harness.version import RUN_SCHEMA_VERSION


def version_info() -> dict:
    """The three version regimes this build ships as: the **package**
    (``importlib.metadata`` -- the same string ``pip show nethackers``
    reports), the **run/publish format** (``RUN_SCHEMA_VERSION``, unrelated
    to the package version -- see ``harness/version.py``), and the **pinned
    sandbox images** this build was cut against (``_image_pins`` -- what a
    fresh install pulls, not necessarily what's locally present already)."""
    return {
        "nethackers": _pkg_version("nethackers"),
        "run_schema_version": RUN_SCHEMA_VERSION,
        "images": {"arena": _image_pins.ARENA_IMAGE, "mutator": _image_pins.MUTATOR_IMAGE},
    }
