"""Tests for ``nethackers.hubclient._deadline.call_with_deadline`` -- the
hard wall-clock guard that bounds a network call the socket/httpx timeout
can't (a hung ``getaddrinfo`` DNS lookup is not covered by any socket
timeout -- CPython resolves the name before applying it). It runs the call on
a daemon thread and gives up after the deadline, so a broken-DNS machine gets
a fast, clear failure instead of an un-interruptible hang (issue #50)."""
from __future__ import annotations

import time

import pytest

from nethackers.hubclient._deadline import call_with_deadline


def test_returns_the_result_when_the_call_finishes_in_time():
    assert call_with_deadline(lambda: 42, 1.0) == 42


def test_raises_timeout_and_gives_up_promptly_when_the_call_overruns():
    def slow():
        time.sleep(0.5)
        return "should never be returned"

    start = time.monotonic()
    with pytest.raises(TimeoutError):
        call_with_deadline(slow, 0.02)
    # Proves it actually BOUNDED the call rather than waiting out slow(): if
    # the deadline were ignored this would take ~0.5s, not ~0.02s.
    assert time.monotonic() - start < 0.4


def test_propagates_an_exception_raised_by_the_call():
    class Boom(Exception):
        pass

    def boom():
        raise Boom("kaboom")

    with pytest.raises(Boom, match="kaboom"):
        call_with_deadline(boom, 1.0)
