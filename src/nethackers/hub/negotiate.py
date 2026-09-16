"""Accept-header negotiation for the one resource this hub serves two ways.

`GET /` has an HTML representation (the website) and a markdown one (the
agent brief, ``views/brief.py``). RFC 9110 leaves the choice to the server
when a client lists several types at the same quality -- Claude Code sends
``text/markdown, text/html, */*``, a three-way tie at q=1 -- so the rule is
ours to state. It lives here rather than inline in ``api.py`` because it is
pure string handling with a large truth table, and that is worth testing
without a TestClient.

The rule (design 2026-09-16, D2): markdown iff the client listed
``text/markdown`` EXPLICITLY with q>0 and nothing it listed carries a
strictly higher q. A wildcard is not a request -- ``*/*`` and ``text/*``
never select markdown -- so browsers and curl keep the page (I1). Never 406:
a landing page answers everyone.
"""

from __future__ import annotations

MARKDOWN = "text/markdown"


def _quality(params: list[str]) -> float:
    """The q of one media range, defaulting to 1.0 (RFC 9110 Sec 12.5.1).

    A malformed q is treated as absent rather than raising. The header is
    attacker-controlled input and this function runs on the front page.
    """
    for param in params:
        name, _, value = param.partition("=")
        if name.strip().lower() == "q":
            try:
                return float(value.strip())
            except ValueError:
                return 1.0
    return 1.0


def prefers_markdown(accept: str | None) -> bool:
    """True iff the client explicitly asked for markdown and nothing it listed
    outranks it. A missing header is False: RFC 9110 lets us answer it with
    anything, and the page is what a browser wants."""
    if not accept:
        return False
    markdown_q: float | None = None
    best_other = 0.0
    for part in accept.split(","):
        media_range, *params = part.split(";")
        media_range = media_range.strip().lower()
        if not media_range:
            continue
        quality = _quality(params)
        if quality <= 0:
            continue
        if media_range == MARKDOWN:
            markdown_q = quality if markdown_q is None else max(markdown_q, quality)
        else:
            best_other = max(best_other, quality)
    return markdown_q is not None and markdown_q >= best_other
