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
    # --workers is resolved at run time, not parse time, so the machine the
    # parser is imported on never leaks into the default.
    assert ns.workers is None


def test_hub_parser_accepts_explicit_workers():
    from nethackers.hub.server import build_parser

    assert build_parser().parse_args(["--workers", "6"]).workers == 6


def test_default_workers_reads_the_cgroup_quota(tmp_path, monkeypatch):
    """A container sees the HOST's core count via os.cpu_count(), not its own
    cgroup quota. Defaulting to os.cpu_count() inside `cpus: 1.0` would fork a
    worker per host core into a one-core budget -- the opposite of the fix. The
    quota wins wherever the kernel exposes one."""
    from nethackers.hub import server

    monkeypatch.setattr(server.os, "cpu_count", lambda: 64)

    v2 = tmp_path / "cpu.max"
    v2.write_text("400000 100000\n")          # 4 cores
    monkeypatch.setattr(server, "_CGROUP_V2", v2)
    monkeypatch.setattr(server, "_CGROUP_V1_QUOTA", tmp_path / "absent")
    assert server.default_workers() == 4

    v2.write_text("150000 100000\n")          # 1.5 cores -> floor, never 0
    assert server.default_workers() == 1

    v2.write_text("max 100000\n")             # unlimited -> fall back to the host
    assert server.default_workers() == 64


def test_default_workers_falls_back_to_cgroup_v1_then_host(tmp_path, monkeypatch):
    from nethackers.hub import server

    monkeypatch.setattr(server.os, "cpu_count", lambda: 8)
    monkeypatch.setattr(server, "_CGROUP_V2", tmp_path / "absent")

    quota, period = tmp_path / "quota", tmp_path / "period"
    quota.write_text("200000\n")   # 2 cores
    period.write_text("100000\n")
    monkeypatch.setattr(server, "_CGROUP_V1_QUOTA", quota)
    monkeypatch.setattr(server, "_CGROUP_V1_PERIOD", period)
    assert server.default_workers() == 2

    quota.write_text("-1\n")                  # v1's "no limit" sentinel
    assert server.default_workers() == 8

    monkeypatch.setattr(server, "_CGROUP_V1_QUOTA", tmp_path / "absent")
    assert server.default_workers() == 8       # bare metal: just the host count


def test_default_workers_is_never_zero(monkeypatch):
    from nethackers.hub import server

    monkeypatch.setattr(server, "_CGROUP_V2", server.Path("/nonexistent"))
    monkeypatch.setattr(server, "_CGROUP_V1_QUOTA", server.Path("/nonexistent"))
    monkeypatch.setattr(server.os, "cpu_count", lambda: None)
    assert server.default_workers() == 1


def test_worker_stub_exits():
    from nethackers.worker import server as ws

    with pytest.raises(SystemExit):
        ws.main([])
