"""``nethackers-worker`` -- the hidden-seed verifier (operator tool).

Three modes, chosen by the CLI args: a one-shot verify of a single
``repo@commit`` (the positional ``program`` arg, then exit); a single
candidate pass (``--once``); or the default daemon loop (fetch config ->
fetch candidates -> verify each, forever).

``HubClient`` and ``verify_program`` are imported at **module level** (not
lazily inside ``main``, unlike ``nethackers.hub.server``) so
``tests/test_verify_worker.py`` can ``monkeypatch.setattr(server,
"HubClient", ...)``/``"verify_program"`` and have ``main`` see the fakes --
that only works because ``main`` looks up the bare names on this module's
namespace at call time rather than referencing the imported-from modules
directly.
"""
from __future__ import annotations

import argparse
import os
import time

from nethackers.hubclient.client import HubClient
from nethackers.worker.verify import verify_program


def _parse_reference(repo_at_commit: str) -> dict[str, str]:
    """Split a one-shot ``"repo@commit"`` CLI arg into the
    ``{"repo","commit"}`` shape the hub and ``verify_program`` expect.
    ``rpartition`` splits on the *last* ``@`` (a repo host/path never
    contains one, but this stays robust either way). Malformed input --
    missing repo or commit half -- is a clear ``SystemExit``, not a
    ``KeyError`` surfacing later."""
    repo, _sep, commit = repo_at_commit.rpartition("@")
    if not repo or not commit:
        raise SystemExit("expected 'repo@commit'")
    return {"repo": repo, "commit": commit}


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="nethackers-worker")
    p.add_argument("program", nargs="?", help="one-shot: a repo@commit to verify, then exit")
    p.add_argument("--hub", default=os.environ.get("NETHACKERS_HUB", "http://localhost:8000"))
    p.add_argument("--token", default=os.environ.get("NETHACKERS_VERIFIER_TOKEN"))
    p.add_argument("--once", action="store_true", help="process one candidate pass then exit")
    p.add_argument("--limit", type=int, default=8)
    return p


def main(argv: list[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    if not args.token:
        raise SystemExit("a verifier token is required (--token or NETHACKERS_VERIFIER_TOKEN)")
    client = HubClient(args.hub)
    config = client.get_verify_config(args.token)

    if args.program:  # one-shot: verify the given reference, then exit
        verify_program(client, args.token, config, _parse_reference(args.program))
        return

    # Daemon (default) / --once: fetch-config -> fetch-candidates -> verify
    # each, forever. Resilient on two axes: hub outages back off
    # exponentially (base 10s, capped at 600s) instead of hot-looping or
    # crashing the daemon, and a per-program verify failure never raises --
    # verify_program already reports it as a "failed" attempt and returns a
    # status string -- so one bad program never stops the rest of the batch.
    backoff = 10.0
    while True:
        try:
            config = client.get_verify_config(args.token)
            candidates = client.get_verify_candidates(args.token, limit=args.limit)
            backoff = 10.0
        except Exception:
            time.sleep(backoff)
            backoff = min(backoff * 2, 600.0)
            continue
        if not candidates:
            if args.once:
                return
            time.sleep(30.0)
            continue
        for cand in candidates:
            verify_program(client, args.token, config, cand["reference"])  # never raises
        if args.once:
            return
