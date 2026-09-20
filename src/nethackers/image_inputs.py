"""The mutator image's content fingerprint: one hash over everything its build
reads and the platform it builds for, so CI, the release gate and a repo
checkout agree on which image a set of files needs.

Stdlib only -- CI runs it with a bare ``python3``::

    PYTHONPATH=src python3 -m nethackers.image_inputs mutator --base REF
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

SCHEME = "nethackers-mutator-inputs/v2"

# The one platform both sandboxes run on. The arena pin is this platform's
# manifest (spec 2026-09-14 D1/D2), and the mutator is built for it too: the
# coding agent scores its own candidates with the arena kit inside the mutator,
# and NetHack generates a different dungeon from the same seed on another
# architecture, so a mutator on any other platform optimizes games the arena
# never plays. Part of the fingerprint (v2) because the same files built for
# another platform are different bytes.
REFERENCE_PLATFORM = "linux/amd64"

# Every path Dockerfile.mutator COPYs, relative to the repo root, plus the
# Dockerfile itself; a directory covers every file beneath it.
# tests/test_image_inputs.py checks this against the Dockerfile's COPY lines.
MUTATOR_INPUT_PATHS: tuple[str, ...] = (
    "Dockerfile.mutator",
    "docker-entrypoint.sh",
    "src/nethackers/__init__.py",
    "src/nethackers/arena",
    "src/nethackers/contracts",
)

# The .dockerignore entries that can occur inside those paths. None of them
# reaches the image, so none may move the fingerprint.
_IGNORED_NAMES = frozenset({"__pycache__", ".DS_Store"})

REPO_ROOT = Path(__file__).resolve().parents[2]


def _ignored(rel: Path) -> bool:
    return bool(_IGNORED_NAMES.intersection(rel.parts)) or rel.suffix == ".pyc"


def _input_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for rel in MUTATOR_INPUT_PATHS:
        path = root / rel
        if path.is_dir():
            files += [f for f in path.rglob("*")
                      if f.is_file() and not _ignored(f.relative_to(root))]
        else:
            files.append(path)
    return sorted(files, key=lambda f: f.relative_to(root).as_posix())


def mutator_inputs_hash(root: Path, base_image: str,
                        platform: str = REFERENCE_PLATFORM) -> str:
    """``sha256:<64 hex>`` over the mutator's build inputs under ``root``, the
    base image it builds on and the platform it is built for. The same files on
    the same base for the same platform give the same value; a change to any
    file's content or executable bit, a new file, a different base or a
    different platform moves it. Raises ``FileNotFoundError`` for a missing file."""
    digest = hashlib.sha256(f"{SCHEME}\n{base_image}\n{platform}\n".encode())
    for path in _input_files(root):
        rel = path.relative_to(root).as_posix()
        mode = "x" if path.stat().st_mode & 0o111 else "-"
        content = hashlib.sha256(path.read_bytes()).hexdigest()
        digest.update(f"{rel}\0{mode}\0{content}\n".encode())
    return "sha256:" + digest.hexdigest()


def image_tag(inputs_hash: str) -> str:
    """The image tag for a fingerprint: ``h-<64 hex>``."""
    return "h-" + inputs_hash.removeprefix("sha256:")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m nethackers.image_inputs",
                                     description="Print an image's input fingerprint.")
    parser.add_argument("kind", choices=["mutator"])
    parser.add_argument("--base", help="base image ref (default: the pinned NLE_BASE_IMAGE)")
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help="repo root to hash")
    args = parser.parse_args(argv)
    base = args.base
    if base is None:
        from nethackers import _image_pins  # stdlib-only generated module
        base = _image_pins.NLE_BASE_IMAGE
    print(mutator_inputs_hash(args.root, base))
    return 0


if __name__ == "__main__":
    sys.exit(main())
