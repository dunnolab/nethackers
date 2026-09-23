"""The link-preview card: served from package data at /social-preview.png, and
pointed at by the page head's og:image / twitter:image so a shared link unfurls
with the large card rather than a bare text summary."""
from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from nethackers.hub.api import create_app
from nethackers.hub.auth import LocalStubAuth
from nethackers.hub.store import Store

_CARD = "https://nethackers.dunnolab.ai/social-preview.png"


def _app(tmp_path: Any) -> TestClient:
    store = Store(tmp_path / "h.db")
    store.init_schema()
    return TestClient(create_app(store, LocalStubAuth({"t": "sam"})))


def test_social_preview_is_served_as_png(tmp_path: Any) -> None:
    r = _app(tmp_path).get("/social-preview.png")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content.startswith(b"\x89PNG\r\n\x1a\n")
    assert "max-age" in r.headers["cache-control"]


def test_social_preview_answers_head_like_get_without_a_body(tmp_path: Any) -> None:
    # X's crawler probes og:image with HEAD before fetching it; a 405 there
    # (the old GET-only route) rendered the card without the image.
    c = _app(tmp_path)
    head, get = c.head("/social-preview.png"), c.get("/social-preview.png")
    assert head.status_code == 200
    assert head.headers["content-type"] == "image/png"
    assert head.headers["content-length"] == get.headers["content-length"]
    assert head.content == b""


def test_page_head_points_the_card_at_the_served_image(tmp_path: Any) -> None:
    head = _app(tmp_path).get("/").text.split("<style>")[0]
    assert f'<meta property="og:image" content="{_CARD}">' in head
    assert f'<meta name="twitter:image" content="{_CARD}">' in head
    assert '<meta name="twitter:card" content="summary_large_image">' in head
    assert '<meta property="og:image:width" content="1280">' in head
    assert '<meta property="og:image:height" content="640">' in head


def test_hacker_page_keeps_the_shared_card(tmp_path: Any) -> None:
    """Personalizing a /h/<username> card rewrites the text tags only; the
    image is the site's one card for every page."""
    head = _app(tmp_path).get("/h/sam").text.split("<style>")[0]
    assert f'<meta property="og:image" content="{_CARD}">' in head
    assert '<meta property="og:title" content="@sam on NetHackers">' in head
