from nethackers import cli
from nethackers.hubclient import credentials as cred
from nethackers.hubclient.publish import PublishError


def _login(monkeypatch, tmp_path, login="sam"):
    monkeypatch.setattr(cred, "path", lambda: tmp_path / "credentials.json")
    cred.save(cred.Credentials(login, "ghu_x", "ghr_y", expires_at=None))


class _FakeHub:
    def __init__(self, base):
        self.base = base
        self.calls: list[dict] = []

    def register(self, *, token, repo, commit, root):
        self.calls.append({"token": token, "repo": repo, "commit": commit, "root": root})
        return {"solution_id": f"{repo}@{commit}"}


def _wire_gh(monkeypatch, *, gh="sam", sha="c" * 40):
    monkeypatch.setattr(cli, "gh_login", lambda: gh)
    monkeypatch.setattr(cli, "ensure_repo", lambda slug: None)
    monkeypatch.setattr(cli, "publish_solution", lambda d, slug, *, message: sha)


def test_submit_publishes_and_registers(monkeypatch, tmp_path, capsys):
    _login(monkeypatch, tmp_path)
    _wire_gh(monkeypatch, gh="sam", sha="c" * 40)
    hubs: list[_FakeHub] = []

    def _mk(base):
        h = _FakeHub(base)
        hubs.append(h)
        return h

    monkeypatch.setattr(cli, "HubClient", _mk)
    rc = cli.main(["submit", str(tmp_path / "sol"), "--hub", "http://h", "-o", "json"])
    assert rc == 0
    assert hubs[0].calls[0] == {
        "token": "ghu_x", "repo": "github.com/sam/nethacker", "commit": "c" * 40, "root": "",
    }


def test_submit_uses_repo_name_flag(monkeypatch, tmp_path):
    _login(monkeypatch, tmp_path)
    _wire_gh(monkeypatch, gh="sam", sha="d" * 40)
    seen = {}
    monkeypatch.setattr(cli, "ensure_repo", lambda slug: seen.setdefault("slug", slug))
    monkeypatch.setattr(cli, "HubClient", lambda base: _FakeHub(base))
    rc = cli.main(["submit", str(tmp_path / "sol"), "--repo-name", "nethacker-dev"])
    assert rc == 0
    assert seen["slug"] == "sam/nethacker-dev"


def test_submit_not_logged_in(monkeypatch, tmp_path):
    monkeypatch.setattr(cred, "path", lambda: tmp_path / "none.json")
    assert cli.main(["submit", "x"]) == 1


def test_submit_gh_unavailable(monkeypatch, tmp_path):
    _login(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "gh_login", lambda: None)
    assert cli.main(["submit", "x"]) == 1


def test_submit_gh_account_mismatch(monkeypatch, tmp_path):
    _login(monkeypatch, tmp_path, login="sam")
    monkeypatch.setattr(cli, "gh_login", lambda: "eve")
    assert cli.main(["submit", "x"]) == 1


def test_submit_publish_error_is_friendly(monkeypatch, tmp_path):
    _login(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "gh_login", lambda: "sam")
    monkeypatch.setattr(cli, "ensure_repo", lambda slug: None)

    def boom(d, slug, *, message):
        raise PublishError("gh is not installed")

    monkeypatch.setattr(cli, "publish_solution", boom)
    assert cli.main(["submit", "x"]) == 1
