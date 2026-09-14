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


def test_opencode2_text_tool_output_and_final_finish_events():
    text = json.dumps({"type": "text", "part": {"type": "text", "text": "Done."}})
    tool = json.dumps({
        "type": "tool_use",
        "part": {"tool": "shell", "state": {
            "status": "completed",
            "input": {"command": "pytest -q"},
            "output": "collecting ...\n3 passed in 0.12s",
        }},
    })
    finish = json.dumps({
        "type": "step_finish",
        "part": {"reason": "stop", "tokens": {"output": 180, "reasoning": 29}},
    })
    assert prettify("opencode2", text) == [("assistant", "Done.")]
    assert prettify("opencode2", tool) == [
        ("tool", "shell pytest -q"),
        ("meta", "↳ collecting ...\n3 passed in 0.12s"),
    ]
    assert prettify("opencode2", finish) == [("result", "done · stop · 209 tok")]


def test_opencode2_tool_call_step_finish_is_not_a_fake_done_line():
    line = json.dumps({
        "type": "step_finish",
        "part": {"reason": "tool-calls", "tokens": {"output": 38, "reasoning": 0}},
    })
    assert prettify("opencode2", line) == []


def test_opencode2_tool_error_and_search_arguments_are_visible():
    line = json.dumps({
        "type": "tool_use",
        "part": {"tool": "grep", "state": {
            "status": "error",
            "input": {"pattern": "TODO", "path": "/workspace/src"},
            "error": "permission denied",
        }},
    })
    assert prettify("opencode2", line) == [
        ("tool", "grep TODO in /workspace/src"),
        ("meta", "error: permission denied"),
    ]


def test_opencode2_emitted_reasoning_and_provider_errors_are_visible():
    reasoning = json.dumps({
        "type": "reasoning",
        "part": {"type": "reasoning", "text": "Checking the failing test."},
    })
    error = json.dumps({
        "type": "error",
        "error": {"name": "ProviderError", "message": "model unavailable"},
    })
    assert prettify("opencode2", reasoning) == [
        ("meta", "thinking: Checking the failing test."),
    ]
    assert prettify("opencode2", error) == [("result", "error: model unavailable")]


def test_opencode2_tool_output_is_bounded():
    output = "\n".join(f"line {number}" for number in range(20))
    line = json.dumps({
        "type": "tool_use",
        "part": {"tool": "read", "state": {
            "input": {"filePath": "/workspace/a.py"}, "output": output,
        }},
    })
    shown = prettify("opencode2", line)
    assert shown[0] == ("tool", "read /workspace/a.py")
    assert "line 0" in shown[1][1]
    assert "line 19" not in shown[1][1]
    assert "14 more lines" in shown[1][1]


def test_opencode2_long_output_keeps_the_end_it_is_trimmed_for():
    # Shell output is trimmed from the front because its summary is at the end;
    # file previews from the back. Long lines must not flip either around.
    first, last = "a" * 300, "===== 1 failed, 7 passed ====="
    output = "\n".join([first] + ["x" * 300] * 7 + [last])

    def shown(tool: str) -> str:
        line = json.dumps({"type": "tool_use", "part": {"tool": tool, "state": {
            "input": {"command": "pytest"}, "output": output,
        }}})
        return prettify("opencode2", line)[1][1]

    assert shown("bash").endswith(last)
    assert shown("read").startswith(f"↳ {first[:50]}")
    assert last not in shown("read")


def test_long_commands_are_not_truncated():
    # regression: commands used to be clipped at 80 chars with a "…"; the agent
    # log must show the full command (RichLog wraps) so nothing is hidden.
    long_cmd = "sed -n '1,320p' /opt/nethackers/src/nethackers/arena/run.py && " \
               "rg -n 'evaluate|progress|milestone' /opt/nethackers/src/nethackers/eval/runner.py"
    line = json.dumps({"type": "item.started",
                       "item": {"type": "command_execution", "command": long_cmd}})
    assert prettify("codex", line) == [("tool", long_cmd)]
    assert "…" not in prettify("codex", line)[0][1]


def test_brief_event_heads_the_log_backend_agnostic():
    # the loop emits a synthetic brief event; it renders as a "brief" line for
    # either backend, with a header so a reader sees the iteration's instruction.
    line = json.dumps({"type": "nethackers_brief", "text": "Improve progression as val-wiz."})
    for backend in ("codex", "claude", "opencode2"):
        out = prettify(backend, line)
        assert len(out) == 1
        kind, text = out[0]
        assert kind == "brief"
        assert "brief" in text and "Improve progression as val-wiz." in text


def test_brief_event_empty_text_skipped():
    assert prettify("codex", json.dumps({"type": "nethackers_brief", "text": "  "})) == []
    assert prettify("codex", json.dumps({"type": "nethackers_brief"})) == []
