import stat

from nethackers.hubclient import credentials as cred


def test_save_load_clear_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(cred, "path", lambda: tmp_path / "credentials.json")
    assert cred.load() is None
    cred.save(cred.Credentials(login="castiel", token="tok-abc"))
    got = cred.load()
    assert got == cred.Credentials(login="castiel", token="tok-abc")
    mode = stat.S_IMODE((tmp_path / "credentials.json").stat().st_mode)
    assert mode == 0o600
    cred.clear()
    assert cred.load() is None


def test_load_tolerates_garbage(tmp_path, monkeypatch):
    p = tmp_path / "credentials.json"
    p.write_text("not json")
    monkeypatch.setattr(cred, "path", lambda: p)
    assert cred.load() is None


def test_whoami_from_token_resolves_login():
    class FakeResp:
        status_code = 200

        def json(self):
            return {"login": "castiel"}

    class FakeHttp:
        def get(self, url, headers):
            assert headers["Authorization"] == "Bearer tok-abc"
            return FakeResp()

    assert cred.whoami_from_token("tok-abc", http=FakeHttp()) == "castiel"
