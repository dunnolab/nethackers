"""The ``nethackers-hub`` console entrypoint.

A thin argparse + uvicorn launcher over ``create_default_app`` (the
environment-configured FastAPI factory in ``nethackers.hub.api``). ``uvicorn``
and the app factory are imported lazily inside ``main`` so that importing this
module -- and asserting parser defaults in ``tests/hub/test_server.py`` -- never
pulls in uvicorn or opens a database.
"""

from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="nethackers-hub")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8000)
    return p


def main(argv: list[str] | None = None) -> None:
    import uvicorn

    from nethackers.hub.api import create_default_app

    ns = build_parser().parse_args(argv)
    uvicorn.run(create_default_app, factory=True, host=ns.host, port=ns.port)
