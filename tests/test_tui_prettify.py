from nethackers.tui.prettify import prettify


def _asst(*blocks):
    import json
    return json.dumps({"type": "assistant", "message": {"content": list(blocks)}})


def test_assistant_text():
    assert prettify("claude", _asst({"type": "text", "text": "Hello world"})) == \
        [("assistant", "Hello world")]


def test_tool_use_edit_read_bash():
    assert prettify("claude", _asst(
        {"type": "tool_use", "name": "Edit", "input": {"file_path": "src/bot.py"}})) == \
        [("tool", "edit src/bot.py")]
    assert prettify("claude", _asst(
        {"type": "tool_use", "name": "Read", "input": {"file_path": "a.py"}})) == \
        [("tool", "read a.py")]
    assert prettify("claude", _asst(
        {"type": "tool_use", "name": "Bash", "input": {"command": "pytest -q"}})) == \
        [("tool", "bash pytest -q")]


def test_text_and_tool_in_one_message_preserve_order():
    out = prettify("claude", _asst(
        {"type": "text", "text": "ok"},
        {"type": "tool_use", "name": "Read", "input": {"file_path": "a.py"}}))
    assert out == [("assistant", "ok"), ("tool", "read a.py")]


def test_result_line():
    line = '{"type":"result","subtype":"success","usage":{"output_tokens":8410}}'
    assert prettify("claude", line) == [("result", "done · success · 8,410 tok")]


def test_system_user_and_empty_text_skipped():
    assert prettify("claude", '{"type":"system","subtype":"init"}') == []
    assert prettify("claude", '{"type":"user","message":{"content":[]}}') == []
    assert prettify("claude", _asst({"type": "text", "text": "   "})) == []


def test_never_crashes_on_bad_shapes():
    assert prettify("claude", "not json") == []
    assert prettify("claude", "[1,2,3]") == []
    assert prettify("claude", '{"type":"assistant","message":"init"}') == []  # message is str
    assert prettify("claude", _asst("not-a-dict")) == []


def test_unknown_backend_is_empty():
    assert prettify("mystery", _asst({"type": "text", "text": "x"})) == []
