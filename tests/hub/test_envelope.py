from datetime import datetime

from nethackers.hub.envelope import envelope


def test_envelope_wraps_rows_with_context_and_timestamp():
    env = envelope([{"a": 1}], scope="generalist", tier="self-reported")
    assert env["rows"] == [{"a": 1}]
    assert env["scope"] == "generalist"
    assert env["tier"] == "self-reported"
    # generated_at parses as an ISO-8601 UTC instant
    datetime.fromisoformat(env["generated_at"].replace("Z", "+00:00"))


def test_envelope_omits_none_context_keys():
    env = envelope([], scope="generalist", identity=None)
    assert "identity" not in env
    assert env["rows"] == []
