"""Tests for the ``nethackers-hub`` entrypoint and the ``nethackers-worker``
Milestone-2 stub. ``build_parser`` is asserted without starting uvicorn, and
the worker stub is confirmed to raise ``SystemExit``.
"""

from __future__ import annotations

import pytest


def test_hub_parser_defaults():
    from nethackers.hub.server import build_parser

    ns = build_parser().parse_args([])
    assert ns.host == "0.0.0.0" and ns.port == 8000


def test_worker_stub_exits():
    from nethackers.worker import server as ws

    with pytest.raises(SystemExit):
        ws.main([])
