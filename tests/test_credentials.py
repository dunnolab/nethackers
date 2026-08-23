import json
import stat

from nethackers.hubclient import credentials as cred


def test_save_load_clear_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(cred, "path", lambda: tmp_path / "credentials.json")
    assert cred.load() is None
    cred.save(cred.Credentials(login="castiel", access_token="tok-abc"))
    got = cred.load()
    assert got == cred.Credentials(login="castiel", access_token="tok-abc")
    mode = stat.S_IMODE((tmp_path / "credentials.json").stat().st_mode)
    assert mode == 0o600
    cred.clear()
    assert cred.load() is None


def test_load_tolerates_garbage(tmp_path, monkeypatch):
    p = tmp_path / "credentials.json"
    p.write_text("not json")
    monkeypatch.setattr(cred, "path", lambda: p)
    assert cred.load() is None


def test_load_tolerates_missing_keys(tmp_path, monkeypatch):
    p = tmp_path / "credentials.json"
    p.write_text("{}")
    monkeypatch.setattr(cred, "path", lambda: p)
    assert cred.load() is None


def test_load_tolerates_missing_token_key(tmp_path, monkeypatch):
    p = tmp_path / "credentials.json"
    p.write_text('{"login": "castiel"}')
    monkeypatch.setattr(cred, "path", lambda: p)
    assert cred.load() is None


def test_load_tolerates_non_dict_json(tmp_path, monkeypatch):
    p = tmp_path / "credentials.json"
    p.write_text("[1, 2]")
    monkeypatch.setattr(cred, "path", lambda: p)
    assert cred.load() is None


def test_whoami_from_token_resolves_login():
    class FakeResp:
        status_code = 200

        def json(self):
            return {"login": "castiel"}

    class FakeHttp:
        def get(self, url, headers):
            assert url == "https://api.github.com/user"
            assert headers["Authorization"] == "Bearer tok-abc"
            return FakeResp()

    assert cred.whoami_from_token("tok-abc", http=FakeHttp()) == "castiel"


def test_roundtrip_new_format(tmp_path, monkeypatch):
    monkeypatch.setattr(cred, "path", lambda: tmp_path / "credentials.json")
    creds = cred.Credentials(
        login="sam", access_token="ghu_a", refresh_token="ghr_b", expires_at=1000.0
    )
    cred.save(creds)
    assert cred.load() == creds
    assert oct((tmp_path / "credentials.json").stat().st_mode)[-3:] == "600"


def test_load_migrates_old_format(tmp_path, monkeypatch):
    monkeypatch.setattr(cred, "path", lambda: tmp_path / "credentials.json")
    (tmp_path / "credentials.json").write_text(json.dumps({"login": "sam", "token": "ghu_old"}))
    got = cred.load()
    assert got == cred.Credentials(
        login="sam", access_token="ghu_old", refresh_token=None, expires_at=None
    )


def test_is_expired_skew():
    creds = cred.Credentials("sam", "ghu_a", "ghr_b", expires_at=1000.0)
    assert creds.is_expired(now=1000.0) is True  # within 60s skew
    assert creds.is_expired(now=930.0) is False
    assert cred.Credentials("sam", "ghu_a", None, None).is_expired(now=1e9) is False  # no expiry
