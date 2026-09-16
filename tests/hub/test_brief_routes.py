"""The HTTP surface of the brief: which representation a client gets, and the
header that stops a cache handing it to the wrong one."""

import pytest
from fastapi.testclient import TestClient

from nethackers.hub.api import create_app
from nethackers.hub.auth import LocalStubAuth
from nethackers.hub.store import Store

CLAUDE_CODE = "text/markdown, text/html, */*"
BROWSER = ("text/html,application/xhtml+xml,application/xml;q=0.9,"
           "image/avif,image/webp,*/*;q=0.8")


@pytest.fixture()
def client(tmp_path):
    store = Store(str(tmp_path / "hub.db"))
    store.init_schema()
    return TestClient(create_app(store, LocalStubAuth({})))


def test_a_markdown_client_gets_markdown(client):
    r = client.get("/", headers={"Accept": CLAUDE_CODE})
    assert r.status_code == 200
    assert r.headers["content-type"] == "text/markdown; charset=utf-8"
    assert r.text.startswith("# NetHackers")


def test_a_browser_still_gets_the_page(client):
    r = client.get("/", headers={"Accept": BROWSER})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "<title>NetHackers</title>" in r.text


def test_curl_still_gets_the_page(client):
    r = client.get("/", headers={"Accept": "*/*"})
    assert r.headers["content-type"].startswith("text/html")


def test_the_html_version_is_still_stamped(client):
    """The masthead reads {{version}} from the installed package at serve time
    -- the markdown branch must not have bypassed that substitution."""
    r = client.get("/", headers={"Accept": BROWSER})
    assert "{{version}}" not in r.text


@pytest.mark.parametrize("accept", [CLAUDE_CODE, BROWSER, "*/*"])
def test_vary_accept_on_both_branches(client, accept):
    """I2. Without this a cache keyed on the URL alone hands one visitor's
    markdown to the next visitor's browser."""
    r = client.get("/", headers={"Accept": accept})
    assert "accept" in r.headers["vary"].lower()


def test_head_is_not_405(client):
    """Some fetchers probe before they get; today this is a 405."""
    r = client.head("/", headers={"Accept": CLAUDE_CODE})
    assert r.status_code == 200


@pytest.mark.parametrize("path", ["/index.md", "/llms.txt"])
def test_the_fixed_urls_serve_text_plain(client, path):
    """D4: ChatGPT's reader rejects a text/markdown body as non-renderable and
    Firefox downloads it instead of displaying it. A client that followed a
    link has told us nothing about what it can read."""
    r = client.get(path)
    assert r.status_code == 200
    assert r.headers["content-type"] == "text/plain; charset=utf-8"
    assert r.text.startswith("# NetHackers")


def test_the_two_fixed_urls_are_the_same_document(client):
    assert client.get("/index.md").text == client.get("/llms.txt").text


def test_no_406_is_ever_returned(client):
    r = client.get("/", headers={"Accept": "application/vnd.made-up"})
    assert r.status_code == 200
