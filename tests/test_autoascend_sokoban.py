"""Every bundled Sokoban answer must be executable on its associated map."""

import importlib.util
import runpy
import sys
from collections import deque
from pathlib import Path
from types import ModuleType

import numpy as np
import pytest

SOLVER = Path(__file__).parents[1] / "roots/autoascend/autoascend/soko_solver"
MAPS = runpy.run_path(str(SOLVER / "maps.py"))["maps"]


def _bfs(y, x, *, walkable, walkable_diagonally, can_squeeze):
    # The solver requests orthogonal reachability only. Keep this dependency
    # small so the actual SokoMap can be tested without NLE/Numba/OpenCV.
    assert not walkable_diagonally.any()
    assert not can_squeeze
    distances = np.full(walkable.shape, -1)
    distances[y, x] = 0
    pending = deque([(y, x)])
    while pending:
        y, x = pending.popleft()
        for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            ny, nx = y + dy, x + dx
            if (0 <= ny < walkable.shape[0] and 0 <= nx < walkable.shape[1]
                    and walkable[ny, nx] and distances[ny, nx] == -1):
                distances[ny, nx] = distances[y, x] + 1
                pending.append((ny, nx))
    return distances


@pytest.fixture
def solver(monkeypatch):
    package = ModuleType("isolated_autoascend")
    utils = ModuleType("isolated_autoascend.utils")
    monkeypatch.setattr(utils, "bfs", _bfs, raising=False)
    maps = ModuleType("isolated_autoascend.soko_solver.maps")
    monkeypatch.setattr(maps, "maps", MAPS, raising=False)
    spec = importlib.util.spec_from_file_location(
        "isolated_autoascend.soko_solver", SOLVER / "__init__.py",
        submodule_search_locations=[str(SOLVER)],
    )
    module = importlib.util.module_from_spec(spec)
    for name, value in (("isolated_autoascend", package),
                        ("isolated_autoascend.utils", utils),
                        ("isolated_autoascend.soko_solver", module),
                        ("isolated_autoascend.soko_solver.maps", maps)):
        monkeypatch.setitem(sys.modules, name, value)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("text,answer", list(MAPS.items()), ids=[f"map-{i}" for i in range(8)])
def test_all_precomputed_pushes_are_legal(solver, text, answer):
    board = solver.convert_map(text)
    targets_filled = 0
    for (y, x), (dy, dx) in answer:
        if board.sokomap[y + dy, x + dx] == solver.TARGET:
            targets_filled += 1
        # Checks boulder presence, reachable pushing position and legal target;
        # before the fix every map fails on the very first push.
        board.move(y, x, dy, dx)
    assert targets_filled > 0
