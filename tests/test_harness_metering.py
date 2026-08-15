from nethackers.harness.metering import Meter, TokenUsage, classify

_A = ('{"type":"assistant","message":{"usage":{"input_tokens":2,"output_tokens":3,'
      '"cache_read_input_tokens":16016,"cache_creation_input_tokens":5448}}}')
_R = ('{"type":"result","usage":{"input_tokens":165,"output_tokens":80820,'
      '"cache_read_input_tokens":11137481,"cache_creation_input_tokens":184264}}')


def test_tokenusage_add_and_total():
    u = TokenUsage(1, 2, 3, 4) + TokenUsage(10, 20, 30, 40)
    assert (u.input, u.output, u.cache_creation, u.cache_read) == (11, 22, 33, 44)
    assert u.total == 110


def test_classify_claude_assistant_is_increment():
    kind, u = classify("claude", _A)
    assert kind == "inc" and u.total == 2 + 3 + 16016 + 5448


def test_classify_result_is_total_for_both_backends():
    for backend in ("claude", "codex"):
        kind, u = classify(backend, _R)
        assert kind == "total" and u.cache_read == 11137481 and u.total == 11402730


def test_classify_codex_has_no_per_turn_usage():
    item = '{"type":"item.completed","item":{"type":"agent_message","text":"hi"}}'
    assert classify("codex", item) is None


# codex-cli reports usage only on turn.completed (no `result` line). Real shape:
# input_tokens already includes cached_input_tokens, so total = input + output.
_CX_TURN = ('{"type":"turn.completed","usage":{"input_tokens":303014,'
            '"cached_input_tokens":255744,"cache_write_input_tokens":0,'
            '"output_tokens":2862,"reasoning_output_tokens":1033}}')


def test_classify_codex_turn_completed_is_total():
    kind, u = classify("codex", _CX_TURN)
    assert kind == "total" and u.total == 303014 + 2862


def test_meter_codex_updates_on_turn_completed():
    m = Meter("codex")
    m.observe('{"type":"item.started","item":{"type":"command_execution"}}')
    assert m.usage.total == 0  # item events carry no usage
    m.observe(_CX_TURN)
    assert m.usage.total == 303014 + 2862


def test_classify_never_raises():
    assert classify("claude", "not json") is None
    assert classify("claude", "[1,2,3]") is None
    assert classify("claude", '{"type":"assistant","message":"str"}') is None
    assert classify("claude", '{"type":"result"}') is None  # no usage


def test_meter_claude_accumulates_then_result_replaces():
    m = Meter("claude")
    m.observe(_A)
    m.observe(_A)  # two increments
    assert m.usage.total == 2 * (2 + 3 + 16016 + 5448)
    m.observe(_R)  # authoritative total REPLACES the running sum
    assert m.usage.total == 11402730


def test_meter_codex_zero_until_result():
    m = Meter("codex")
    m.observe('{"type":"item.completed","item":{"type":"agent_message","text":"x"}}')
    assert m.usage.total == 0
    m.observe(_R)
    assert m.usage.total == 11402730
