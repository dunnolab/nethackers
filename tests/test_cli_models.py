import json

from nethackers import cli
from nethackers.harness.discovery import ModelInfo


def test_models_command_lists_live_models_json(capsys, monkeypatch):
    monkeypatch.setattr(cli, "list_models",
                        lambda backend, **k: [ModelInfo("gpt-5.6-sol", "gpt-5.6-sol",
                                                        ("low", "high"), False)])
    rc = cli._run(["models", "--operator", "codex", "-o", "json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out[0]["id"] == "gpt-5.6-sol" and out[0]["reasoning"] == ["low", "high"]


def test_models_command_reports_when_discovery_unavailable(capsys, monkeypatch):
    monkeypatch.setattr(cli, "list_models", lambda backend, **k: None)
    rc = cli._run(["models", "--operator", "claude", "-o", "json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out == []          # unknown -> empty list under json (a note goes to stderr)
