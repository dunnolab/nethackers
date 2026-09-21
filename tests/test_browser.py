import webbrowser

import pytest

from nethackers import browser

# Real stdlib controllers, named the way ``webbrowser.get()`` reports them;
# constructing one launches nothing.
_GUI = webbrowser.BackgroundBrowser("xdg-open")
_LYNX = webbrowser.GenericBrowser("lynx")
_VSCODE_HELPER = webbrowser.GenericBrowser("/home/u/.vscode-server/bin/abc/helpers/browser.sh")


def _no_browser():
    raise webbrowser.Error("could not locate runnable browser")


@pytest.mark.parametrize(
    ("platform", "environ", "get", "want"),
    [
        # plain SSH to a Linux server: no display, so nothing to hand a URL to
        # -- even if webbrowser would find something (a console fallback)
        ("linux", {}, lambda: _GUI, False),
        ("linux", {"DISPLAY": ":0"}, lambda: _GUI, True),
        ("linux", {"WAYLAND_DISPLAY": "wayland-0"}, lambda: _GUI, True),
        # $BROWSER is an explicit choice -- VS Code Remote-SSH points it at a
        # helper that opens the URL on the laptop, no display needed
        ("linux", {"BROWSER": _VSCODE_HELPER.name}, lambda: _VSCODE_HELPER, True),
        # a console browser would take the terminal over, never "your browser"
        ("linux", {"DISPLAY": ":0"}, lambda: _LYNX, False),
        ("linux", {"BROWSER": "lynx"}, lambda: _LYNX, False),
        ("linux", {"DISPLAY": ":0"}, _no_browser, False),
        # macOS and Windows open URLs without an X/Wayland display
        ("darwin", {}, lambda: _GUI, True),
        ("win32", {}, lambda: _GUI, True),
        ("freebsd14", {}, lambda: _GUI, False),
    ],
)
def test_can_open(platform, environ, get, want):
    assert browser.can_open(environ=environ, platform=platform, get=get) is want


def test_can_open_never_raises():
    def broken():
        raise OSError("xdg-settings: exec format error")

    assert browser.can_open(environ={"DISPLAY": ":0"}, platform="linux", get=broken) is False


def test_open_url_hands_the_url_to_the_launcher():
    seen = []

    def launch(url):
        seen.append(url)
        return True

    assert browser.open_url("https://github.com/login/device", launch=launch) is True
    assert seen == ["https://github.com/login/device"]


def test_open_url_reports_a_launcher_that_found_no_browser():
    assert browser.open_url("https://github.com/login/device", launch=lambda _u: False) is False


def test_open_url_never_raises():
    def launch(_url):
        raise OSError("no such file: xdg-open")

    assert browser.open_url("https://github.com/login/device", launch=launch) is False
