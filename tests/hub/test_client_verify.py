from nethackers.hubclient.client import HubClient


class _Resp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


class _Http:
    def __init__(self):
        self.calls = []

    def get(self, url, **kw):
        self.calls.append(("GET", url, kw))
        payload = ({"rows": [{"program_id": "p"}]}
                   if url.endswith("candidates")
                   else {"secret": "s", "seeds": [1]})
        return _Resp(payload)

    def post(self, url, **kw):
        self.calls.append(("POST", url, kw))
        return _Resp({"inserted": 2})


def test_get_verify_config_sends_bearer():
    http = _Http()
    c = HubClient("http://h", http=http)
    assert c.get_verify_config("vt") == {"secret": "s", "seeds": [1]}
    method, url, kw = http.calls[0]
    assert (method, url) == ("GET", "http://h/verify/config")
    assert kw["headers"]["Authorization"] == "Bearer vt"


def test_post_verify_sends_body_and_bearer():
    http = _Http()
    c = HubClient("http://h", http=http)
    out = c.post_verify(
        "vt",
        reference={"repo": "r", "commit": "c"},
        evidence={"tier": "verified"},
        secret_fingerprint="fp",
    )
    assert out == {"inserted": 2}
    method, url, kw = http.calls[0]
    assert (method, url) == ("POST", "http://h/verify")
    assert kw["json"]["secret_fingerprint"] == "fp"
    assert kw["headers"]["Authorization"] == "Bearer vt"
