"""The whole run, with every dependency faked: nothing is installed, pulled,
or logged in to."""
from __future__ import annotations

import io
from dataclasses import replace
from pathlib import Path

from rich.console import Console

from nethackers.containers import RuntimeCandidate, RuntimeReport
from nethackers.diagnostics import CHECK_SPECS, CheckResult
from nethackers.hubclient.credentials import Credentials
from nethackers.setup import flow
from nethackers.setup.host import HostFacts
from nethackers.setup.runner import StepResult

UP = RuntimeReport("docker", (RuntimeCandidate("docker", "usable", ""),))
NONE = RuntimeReport(None, (RuntimeCandidate("docker", "absent", ""),
                            RuntimeCandidate("podman", "absent", "")))
MAC = HostFacts("Darwin", "arm64", brew=True, host_rosetta=True, cpus=10, memory_gb=32)


def checks(**status: str) -> list[CheckResult]:
    return [CheckResult(id=cid, status=status.get(cid, "ok"), severity=sev, detail="d",
                        fix=None, capabilities=caps) for cid, (sev, caps) in CHECK_SPECS.items()]


class Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.reports: list = []


def make(before, after=None, *, runtime=UP, creds=Credentials("you", "t"),  # noqa: B008
         gh=("you", "authed"), agents=None, **over):
    rec = Recorder()
    results = iter([before, after if after is not None else before])
    logged_in = agents if agents is not None else {"claude": True, "codex": False}

    def run_terminal(argv):
        rec.calls.append(("terminal", tuple(argv)))
        return StepResult(True, 1.0)

    def run_captured(argv, title):
        rec.calls.append(("captured", tuple(argv)))
        return StepResult(True, 2.0)

    def pull(kinds, total):
        rec.calls.append(("pull", kinds))
        return None

    def hub_login():
        rec.calls.append(("hub_login",))
        return "you"

    deps = flow.SetupDeps(
        console=Console(file=io.StringIO(), width=120, force_terminal=False),
        run_checks=lambda **kw: next(results),
        detect_host=lambda: MAC,
        probe_runtime=lambda: runtime,
        gh_state=lambda: gh,
        load_creds=lambda: creds,
        agent_logged_in=lambda op: logged_in.get(op, op == "opencode2"),
        resolve_exe=lambda name: f"/usr/local/bin/{name}",
        read_text=lambda path: None,
        hub_login=hub_login,
        pull=pull,
        pull_size=lambda kinds: 875_000_000,
        run_terminal=run_terminal,
        run_captured=run_captured,
        ask_agent=lambda: rec.calls.append(("ask",)) or "claude",
        confirm=lambda: rec.calls.append(("confirm",)) or True,
        report=lambda chk, summary: rec.reports.append(summary),
    )
    return replace(deps, **over), rec


def opts(**kw) -> flow.SetupOptions:
    base: dict = dict(scope=None, operator=None, yes=False, interactive=True, hub="https://h")
    base.update(kw)
    return flow.SetupOptions(**base)


def output(deps) -> str:
    return deps.console.file.getvalue()


def test_a_ready_machine_says_nothing_to_do_and_exits_zero():
    deps, rec = make(checks())
    assert flow.run_setup(opts(), deps) == 0
    assert rec.calls == []
    assert rec.reports[0].nothing_to_do and rec.reports[0].not_ready == ()


def test_without_a_terminal_and_without_yes_nothing_changes():
    deps, rec = make(checks(hub_login="fail"), creds=None)
    assert flow.run_setup(opts(interactive=False), deps) == 1
    assert rec.calls == []
    assert "Nothing changed" in output(deps)


def test_yes_without_a_terminal_runs_unattended_steps_and_lists_the_logins():
    deps, rec = make(checks(hub_login="fail", mutator_image="warn"), creds=None)
    flow.run_setup(opts(interactive=False, yes=True), deps)
    assert ("pull", ("mutator",)) in rec.calls and ("hub_login",) not in rec.calls
    assert rec.reports[0].commands == ("nethackers login",)


def test_answering_no_changes_nothing():
    deps, rec = make(checks(mutator_image="warn"), confirm=lambda: False)
    assert flow.run_setup(opts(), deps) == 1
    assert rec.calls == []


def test_steps_run_in_plan_order_after_one_yes():
    before = checks(container_runtime="fail", arena_image="fail", mutator_image="fail",
                    hub_login="fail")
    deps, rec = make(before, checks(), runtime=NONE, creds=None, gh=(None, "unauthed"))
    assert flow.run_setup(opts(), deps) == 0
    kinds = [c[0] for c in rec.calls]
    assert kinds == ["confirm", "hub_login", "terminal", "captured", "captured", "pull"]
    assert rec.calls[2] == ("terminal", ("/usr/local/bin/gh", "auth", "login", "--hostname",
                                         "github.com", "--git-protocol", "https", "--web"))


def test_a_failed_step_skips_the_steps_that_need_it_but_not_the_others():
    before = checks(container_runtime="fail", mutator_image="warn", gh="fail")
    failing = lambda argv, title: StepResult(False, 1.0, "exited 1", ("boom",))  # noqa: E731
    deps, rec = make(before, runtime=NONE, gh=(None, "unauthed"), run_captured=failing)
    flow.run_setup(opts(), deps)
    text = output(deps)
    assert "skipped" in text and "boom" in text
    assert ("pull", ("mutator",)) not in rec.calls              # needed the runtime
    assert ("terminal", ("/usr/local/bin/gh", "auth", "login", "--hostname", "github.com",
                         "--git-protocol", "https", "--web")) in rec.calls  # didn't


def test_a_step_that_raises_is_a_failed_step_not_a_crash():
    def boom():
        raise RuntimeError("github down")
    deps, rec = make(checks(hub_login="fail", mutator_image="warn"), creds=None, hub_login=boom)
    flow.run_setup(opts(), deps)
    assert "github down" in output(deps)
    assert ("pull", ("mutator",)) in rec.calls


def test_the_agent_question_is_asked_only_in_a_terminal_without_yes():
    none_logged_in = {"claude": False, "codex": False}
    deps, rec = make(checks(), agents=none_logged_in)
    flow.run_setup(opts(), deps)
    assert ("ask",) in rec.calls
    deps, rec = make(checks(), agents=none_logged_in)
    flow.run_setup(opts(yes=True), deps)
    assert ("ask",) not in rec.calls
    assert "--operator" in output(deps)


def test_an_account_mismatch_after_the_logins_fails_that_step():
    deps, rec = make(checks(gh="fail"), gh=(None, "unauthed"))
    deps = replace(deps, gh_state=iter([(None, "unauthed"), ("bob", "authed")]).__next__)
    flow.run_setup(opts(), deps)
    assert "gh is @bob but the hub login is @you" in output(deps)


def test_native_windows_is_not_covered():
    deps, rec = make(checks(), detect_host=lambda: HostFacts("Windows", "AMD64"))
    assert flow.run_setup(opts(), deps) == 1
    assert "WSL2" in output(deps)


def test_exit_code_follows_the_scope():
    assert flow.setup_exit_code(checks(gh="fail"), "eval") == 0
    assert flow.setup_exit_code(checks(gh="fail"), None) == 1


def test_resolve_exe_finds_the_vendor_install_location(tmp_path):
    local = tmp_path / ".local" / "bin"
    local.mkdir(parents=True)
    (local / "claude").write_text("")
    assert flow.resolve_exe("claude", which=lambda n: None, home=tmp_path) == str(local / "claude")
    assert flow.resolve_exe("codex", which=lambda n: None, home=tmp_path) is None
    assert flow.resolve_exe("gh", which=lambda n: "/bin/gh", home=Path("/x")) == "/bin/gh"
