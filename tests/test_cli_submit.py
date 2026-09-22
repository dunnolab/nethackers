from nethackers import cli
from nethackers.hubclient import credentials as cred
from nethackers.hubclient.publish import PublishError


def _login(monkeypatch, tmp_path, login="sam"):
    monkeypatch.setattr(cred, "path", lambda: tmp_path / "credentials.json")
    cred.save(cred.Credentials(login, "ghu_x", "ghr_y", expires_at=None))


# The evidence eval_batch would produce -- stubbed so `submit` never shells out
# to Docker; the register call carries exactly this dict (evidence.to_dict()).
_EVIDENCE = {"solution_digest": "sha256:x"}


class _FakeEvidence:
    def to_dict(self):
        return _EVIDENCE


class _FakeHub:
    def __init__(self, base):
        self.base = base
        self.calls: list[dict] = []

    def register(self, *, token, reference, manifest, evidence):
        self.calls.append({"token": token, "reference": reference,
                           "manifest": manifest, "evidence": evidence})
        return {"solution_id": f"{reference['repo']}@{reference['commit']}"}


def _wire_gh(monkeypatch, *, gh="sam", sha="c" * 40):
    monkeypatch.setattr(cli, "gh_state", lambda: (gh, "authed"))
    monkeypatch.setattr(cli, "ensure_repo", lambda slug: None)
    monkeypatch.setattr(cli, "publish_solution", lambda d, slug, *, message, ref=None: sha)
    monkeypatch.setattr(cli, "eval_batch", lambda *a, **k: _FakeEvidence())
    # submit scores locally exactly like eval (self-reported), so it carries
    # the same arena-only preflight (this task's acquisition gap) -- stub it
    # green so this stays hermetic, never shelling out to Docker.
    monkeypatch.setattr(cli, "preflight_runtime", lambda **kw: None)
    monkeypatch.setattr(cli, "ensure_image", lambda *a, **kw: None)


def test_submit_publishes_and_registers(monkeypatch, tmp_path, capsys, clean_stage):
    _login(monkeypatch, tmp_path)
    _wire_gh(monkeypatch, gh="sam", sha="c" * 40)
    hubs: list[_FakeHub] = []

    def _mk(base):
        h = _FakeHub(base)
        hubs.append(h)
        return h

    monkeypatch.setattr(cli, "HubClient", _mk)
    rc = cli.main(["submit", str(tmp_path / "sol"), "--hub", "http://h",
                   "-o", "json", "--objective", "random"])
    assert rc == 0
    # register got the published repo@commit reference, the default manifest
    # (no nethackers.solution.json in the dir), and the eval evidence dict.
    assert hubs[0].calls[0] == {
        "token": "ghu_x",
        "reference": {"repo": "github.com/sam/nethacker", "commit": "c" * 40},
        "manifest": {"root": ".", "entrypoint": "bot.py", "parents": [], "influences": []},
        "evidence": _EVIDENCE,
    }


def test_submit_publishes_to_its_own_branch(monkeypatch, tmp_path):
    # publish_solution tree-syncs the branch it pushes to. On the default
    # branch that would wipe the repo's landing README, so submit -- like every
    # evolve run -- gets a branch of its own.
    _login(monkeypatch, tmp_path)
    _wire_gh(monkeypatch)
    seen = {}

    def publish(d, slug, *, message, ref=None):
        seen["ref"] = ref
        return "c" * 40

    monkeypatch.setattr(cli, "publish_solution", publish)
    monkeypatch.setattr(cli, "HubClient", lambda base: _FakeHub(base))
    rc = cli.main(["submit", str(tmp_path / "sol"), "--objective", "random"])
    assert rc == 0
    assert seen["ref"] == "submit"


def test_submit_uses_repo_name_flag(monkeypatch, tmp_path):
    _login(monkeypatch, tmp_path)
    _wire_gh(monkeypatch, gh="sam", sha="d" * 40)
    seen = {}
    monkeypatch.setattr(cli, "ensure_repo", lambda slug: seen.setdefault("slug", slug))
    monkeypatch.setattr(cli, "HubClient", lambda base: _FakeHub(base))
    rc = cli.main(["submit", str(tmp_path / "sol"), "--repo-name", "nethacker-dev",
                   "--objective", "random"])
    assert rc == 0
    assert seen["slug"] == "sam/nethacker-dev"


def test_submit_not_logged_in(monkeypatch, tmp_path):
    monkeypatch.setattr(cred, "path", lambda: tmp_path / "none.json")
    assert cli.main(["submit", "x", "--objective", "random"]) == 1


def test_submit_gh_unavailable(monkeypatch, tmp_path, capsys):
    # gh not installed: install-gh fix, distinct from the unauthed case below.
    _login(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "gh_state", lambda: (None, "missing"))
    rc = cli.main(["submit", "x", "--objective", "random"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "gh not installed" in err and "install the GitHub CLI" in err


def test_submit_gh_unauthed(monkeypatch, tmp_path, capsys):
    # gh installed but not logged in: `gh auth login` fix, distinct from the
    # not-installed case above -- exactly the conflation this task splits.
    _login(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "gh_state", lambda: (None, "unauthed"))
    rc = cli.main(["submit", "x", "--objective", "random"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "gh not authed" in err and "gh auth login" in err
    assert "nethackers setup --for publish" in err


def test_submit_gh_did_not_answer(monkeypatch, tmp_path, capsys):
    # gh timed out: neither "not installed" nor "not authed" -- say what happened.
    _login(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "gh_state", lambda: (None, "unknown"))
    rc = cli.main(["submit", "x", "--objective", "random"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "didn't answer" in err
    assert "not authed" not in err and "not installed" not in err


def test_submit_gh_account_mismatch(monkeypatch, tmp_path, capsys):
    _login(monkeypatch, tmp_path, login="sam")
    monkeypatch.setattr(cli, "gh_state", lambda: ("eve", "authed"))
    rc = cli.main(["submit", "x", "--objective", "random"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "sign in to the same account" in err


def test_submit_publish_error_is_friendly(monkeypatch, tmp_path):
    _login(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "gh_state", lambda: ("sam", "authed"))
    monkeypatch.setattr(cli, "ensure_repo", lambda slug: None)
    monkeypatch.setattr(cli, "eval_batch", lambda *a, **k: _FakeEvidence())
    monkeypatch.setattr(cli, "preflight_runtime", lambda **kw: None)
    monkeypatch.setattr(cli, "ensure_image", lambda *a, **kw: None)

    def boom(d, slug, *, message, ref=None):
        raise PublishError("gh is not installed")

    monkeypatch.setattr(cli, "publish_solution", boom)
    assert cli.main(["submit", "x", "--objective", "random"]) == 1
