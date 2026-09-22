"""The whole run, with every dependency faked: nothing is installed, pulled,
or logged in to."""
from __future__ import annotations

import io
from dataclasses import replace
from pathlib import Path

import pytest
from rich.console import Console

from nethackers.containers import RuntimeCandidate, RuntimeReport
from nethackers.diagnostics import CHECK_SPECS, CheckResult
from nethackers.hubclient.credentials import Credentials
from nethackers.setup import flow, render
from nethackers.setup.host import HostFacts
from nethackers.setup.runner import StepResult

UP = RuntimeReport("docker", (RuntimeCandidate("docker", "usable", ""),))
NONE = RuntimeReport(None, (RuntimeCandidate("docker", "absent", ""),
                            RuntimeCandidate("podman", "absent", "")))
MAC = HostFacts("Darwin", "arm64", brew=True, host_rosetta=True, cpus=10, memory_gb=32)
UBUNTU = HostFacts("Linux", "x86_64", distro="debian", distro_name="Ubuntu 24.04 LTS")
DESKTOP_MAC = replace(MAC, installed=frozenset({"docker", "docker-desktop"}),
                      docker_context="desktop-linux", docker_desktop_cli=True)
ROSETTA_OFF = '{"UseVirtualizationFramework": true, "UseVirtualizationFrameworkRosetta": false}'


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
        which=lambda name: f"/usr/local/bin/{name}",
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


def test_yes_without_a_terminal_lists_the_logins_on_the_console_not_in_the_summary():
    # A coding agent's stdout is a pipe (doctor's JSON goes there), so the
    # logins it has to run itself are printed on the console (stderr).
    deps, rec = make(checks(hub_login="fail", gh="fail", mutator_image="warn"), creds=None,
                     gh=(None, "unauthed"))
    flow.run_setup(opts(interactive=False, yes=True), deps)
    assert ("pull", ("mutator",)) in rec.calls
    assert not [c for c in rec.calls if c[0] in ("hub_login", "terminal")]
    listed = output(deps).split("Run these logins yourself", 1)[1]
    assert "\n  nethackers login\n" in listed
    assert "  gh auth login --hostname github.com --git-protocol https --web" in listed
    summary = render.summary_plain(rec.reports[0])
    assert "Run these logins yourself" not in summary and "gh auth login" not in summary


def test_a_listed_agent_login_names_the_file_when_only_local_bin_has_it():
    # The vendor installer puts claude in ~/.local/bin, which this shell's
    # PATH may not have yet: a bare `claude auth login` would not run.
    local = "/Users/you/.local/bin/claude"
    deps, rec = make(checks(), agents={"claude": False, "codex": False},
                     which=lambda name: None,
                     resolve_exe=lambda name: local if name == "claude" else None)
    flow.run_setup(opts(interactive=False, yes=True, operator="claude"), deps)
    assert f"  {local} auth login" in output(deps).split("Run these logins yourself", 1)[1]
    deps, rec = make(checks(), agents={"claude": False, "codex": False})   # on PATH
    flow.run_setup(opts(interactive=False, yes=True, operator="claude"), deps)
    assert "\n  claude auth login" in output(deps).split("Run these logins yourself", 1)[1]


@pytest.mark.parametrize("interactive", [True, False], ids=["terminal", "no-terminal"])
@pytest.mark.parametrize("case", ["ubuntu-docker-install", "rosetta-off", "two-accounts"])
def test_a_plan_with_nothing_for_nethackers_to_run_never_asks(case, interactive):
    # Only instructions for the person: asking "Continue?" (or suggesting
    # --yes) would promise a run that does nothing.
    if case == "ubuntu-docker-install":
        deps, rec = make(checks(container_runtime="fail", arena_image="warn",
                                mutator_image="warn"), runtime=NONE, detect_host=lambda: UBUNTU)
        said, code = "install Docker", 1
    elif case == "rosetta-off":
        deps, rec = make(checks(rosetta="warn"), detect_host=lambda: DESKTOP_MAC,
                         read_text=lambda path: ROSETTA_OFF)
        said, code = "turn on Rosetta", 0
    else:
        deps, rec = make(checks(), gh=("bob", "authed"))
        said, code = "gh is @bob but the hub login is @you", 0
    assert flow.run_setup(opts(interactive=interactive), deps) == code
    assert rec.calls == []                      # no "confirm", and nothing ran
    assert said in output(deps)
    assert "--yes" not in output(deps) and "Nothing changed" not in output(deps)
    assert len(rec.reports) == 1


def test_yes_in_a_terminal_runs_every_step_without_asking():
    deps, rec = make(checks(hub_login="fail", gh="fail", mutator_image="warn"), checks(),
                     creds=None, gh=(None, "unauthed"))
    assert flow.run_setup(opts(yes=True), deps) == 0
    assert [c[0] for c in rec.calls] == ["hub_login", "terminal", "pull"]


def test_the_run_is_numbered_by_the_plans_own_step_numbers():
    # Without a terminal the hub login (1) isn't run and the account check (2)
    # is skipped; the pull is still step 3 of 3, as the plan showed it.
    deps, rec = make(checks(hub_login="fail", mutator_image="warn"), creds=None)
    flow.run_setup(opts(interactive=False, yes=True), deps)
    assert "[3/3] pull the mutator image" in output(deps)


def test_the_summary_uses_the_checklists_short_words():
    ref = "ghcr.io/dunnolab/nethackers-mutator@sha256:" + "a" * 64
    raw = [replace(c, detail=f"not local yet, but pullable — {ref}")
           if c.id == "mutator_image" else c for c in checks(mutator_image="warn")]
    deps, rec = make(raw, confirm=lambda: False)
    flow.run_setup(opts(), deps)
    assert rec.reports[0].failing == (("mutator image", "not pulled yet"),)


def test_setup_prints_its_own_lines_without_recolouring_numbers():
    console = Console(file=io.StringIO(), width=120, force_terminal=True,
                      color_system="standard")
    deps, rec = make(checks(container_runtime="fail", arena_image="warn", mutator_image="warn"),
                     runtime=NONE, detect_host=lambda: UBUNTU, console=console)
    flow.run_setup(opts(), deps)
    assert "real Ubuntu 24.04 LTS yet" in output(deps)   # "24.04" not highlighted


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
