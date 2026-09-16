"""The Accept rule for the one resource this hub serves two ways.

Strings marked (measured) were captured from real clients on 2026-09-16 or
by the header-capture projects cited in the design. They are verbatim on
purpose: a hand-written approximation would not have caught the tie case.
"""

import pytest

from nethackers.hub.negotiate import prefers_markdown

CLAUDE_CODE = "text/markdown, text/html, */*"                       # measured
CURSOR_A = ("text/markdown,text/html;q=0.9,application/xhtml+xml;q=0.8,"
            "application/xml;q=0.7,image/webp;q=0.6,*/*;q=0.5")     # measured
CURSOR_B = "text/markdown, text/plain;q=0.9, */*;q=0.8"             # measured
COPILOT = ("text/markdown, text/html;q=0.9, application/xhtml+xml;q=0.9, "
           "application/xml;q=0.8, */*;q=0.7")                      # measured
CHATGPT = ("text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
           "image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9")
CURL = "*/*"                                                        # measured


@pytest.mark.parametrize("accept", [CLAUDE_CODE, CURSOR_A, CURSOR_B, COPILOT])
def test_clients_that_ask_for_markdown_get_it(accept):
    assert prefers_markdown(accept) is True


@pytest.mark.parametrize("accept", [CHATGPT, CURL, None, "", "text/html"])
def test_clients_that_do_not_ask_keep_html(accept):
    assert prefers_markdown(accept) is False


def test_a_wildcard_is_not_a_request():
    """`text/*` matches markdown but does not ask for it. Serving markdown to
    a wildcard is how a browser ends up with a text file."""
    assert prefers_markdown("text/*") is False
    assert prefers_markdown("text/*, */*") is False


def test_lower_quality_markdown_loses():
    """The case Stripe gets wrong: it returns markdown here, ignoring q."""
    assert prefers_markdown("text/markdown;q=0.5, text/html") is False


def test_q_zero_is_a_refusal():
    assert prefers_markdown("text/markdown;q=0, text/html") is False


def test_equal_quality_is_a_win_for_markdown():
    """RFC 9110 leaves an all-q=1 tie to the server; every production
    implementation surveyed resolves it to markdown."""
    assert prefers_markdown("text/html, text/markdown") is True


def test_parameters_other_than_q_are_ignored():
    assert prefers_markdown("text/markdown; charset=utf-8") is True


def test_a_malformed_q_does_not_raise():
    """The header is attacker-controlled input."""
    assert prefers_markdown("text/markdown;q=banana") is True
    assert prefers_markdown(";;;") is False
