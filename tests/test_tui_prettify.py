import json

from nethackers.tui.prettify import prettify


def _asst(*blocks):
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


def test_never_raises_on_pathologically_nested_json():
    line = "[" * 100_000 + "]" * 100_000  # json.loads would RecursionError
    assert prettify("claude", line) == []


# ---- codex-cli >=0.1x event stream (nested under "item") ----

def test_codex_agent_message_on_completed():
    line = json.dumps({"type": "item.completed",
                       "item": {"type": "agent_message", "text": "Tuning the policy."}})
    assert prettify("codex", line) == [("assistant", "Tuning the policy.")]


def test_codex_command_execution_on_started_strips_shell_wrapper():
    line = json.dumps({"type": "item.started",
                       "item": {"type": "command_execution",
                                "command": '/bin/zsh -lc "ls -la && pytest -q"'}})
    assert prettify("codex", line) == [("tool", "ls -la && pytest -q")]


def test_codex_command_execution_on_completed_is_deduped():
    # already emitted on item.started; the completed twin must not repeat it
    line = json.dumps({"type": "item.completed",
                       "item": {"type": "command_execution",
                                "command": '/bin/zsh -lc "ls"', "status": "completed"}})
    assert prettify("codex", line) == []


def test_codex_file_change_shows_basenames():
    changes = [{"path": "/x/autoascend/global_logic.py", "kind": "update"},
               {"path": "/x/bot.py", "kind": "update"}]
    line = json.dumps({"type": "item.started",
                       "item": {"type": "file_change", "changes": changes}})
    assert prettify("codex", line) == [("tool", "edit global_logic.py, bot.py")]


def test_codex_thread_and_turn_events_skipped():
    for t in ("thread.started", "turn.started", "turn.completed"):
        assert prettify("codex", json.dumps({"type": t})) == []


def test_codex_legacy_toplevel_fallback_still_works():
    assert prettify("codex", json.dumps({"text": "hi"})) == [("assistant", "hi")]
    assert prettify("codex", json.dumps({"command": "pytest -q"})) == [("tool", "bash pytest -q")]


def test_codex_never_crashes_on_bad_shapes():
    assert prettify("codex", json.dumps({"type": "item.completed", "item": "nope"})) == []
    assert prettify("codex", json.dumps({"type": "item.started",
                    "item": {"type": "file_change", "changes": "weird"}})) == []
    assert prettify("codex", json.dumps({"type": "item.completed",
                    "item": {"type": "agent_message", "text": "   "}})) == []
