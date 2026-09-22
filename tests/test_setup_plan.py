"""Planning is pure: doctor's results + host facts in, an ordered plan out."""
from __future__ import annotations

from dataclasses import replace

from nethackers.containers import RuntimeCandidate, RuntimeReport
from nethackers.diagnostics import CHECK_SPECS, CheckResult
from nethackers.setup import linux, macos
from nethackers.setup.host import HostFacts
from nethackers.setup.plan import GH_LOGIN, Plan, Situation, build_plan, listed_command

UP = RuntimeReport("docker", (RuntimeCandidate("docker", "usable", ""),))
NONE = RuntimeReport(None, (RuntimeCandidate("docker", "absent", ""),
                            RuntimeCandidate("podman", "absent", "")))
MAC = HostFacts("Darwin", "arm64", brew=True, host_rosetta=True, cpus=10, memory_gb=32)
UBUNTU = HostFacts("Linux", "x86_64", distro="debian", distro_name="Ubuntu 24.04 LTS")


def checks(**status: str) -> tuple[CheckResult, ...]:
    """Every doctor check ok, except the ones named: checks(gh="fail")."""
    out = []
    for cid, (severity, caps) in CHECK_SPECS.items():
        s = status.get(cid, "ok")
        out.append(CheckResult(id=cid, status=s, severity=severity, detail="",
                               fix=None if s == "ok" else "x", capabilities=caps))
    return tuple(out)


def sit(**kw) -> Situation:
    base: dict = dict(checks=checks(), facts=MAC, runtime=UP, scope=None, agent="claude",
                      agent_installed=True, agent_logged_in=True, hub_login="you",
                      gh_login="you", gh_state="authed")
    base.update(kw)
    return Situation(**base)


def ids(plan: Plan) -> list[str]:
    return [s.id for s in plan.steps]


def test_a_ready_machine_has_an_empty_plan():
    assert build_plan(sit(), macos).empty


def test_a_fresh_mac_gets_the_logins_first_and_the_slow_work_last():
    plan = build_plan(sit(
        checks=checks(container_runtime="fail", arena_image="fail", mutator_image="fail",
                      hub_login="fail", gh="fail"),
        runtime=NONE, agent_installed=False, agent_logged_in=False,
        hub_login=None, gh_login=None, gh_state="missing"), macos)
    assert ids(plan) == ["hub-login", "gh-install", "gh-login", "same-account",
                         "agent-install", "agent-login", "runtime-1", "runtime-2", "pull"]
    step = {s.id: s for s in plan.steps}
    assert step["gh-login"].argv == GH_LOGIN and step["gh-login"].needs == ("gh-install",)
    assert step["same-account"].needs == ("hub-login", "gh-login")
    assert step["agent-login"].argv == ("claude", "auth", "login")
    assert step["agent-login"].needs == ("agent-install",)
    assert step["runtime-1"].recipe is macos.COLIMA_INSTALL
    assert step["runtime-2"].needs == ("runtime-1",) and step["pull"].needs == ("runtime-2",)
    assert step["pull"].images == ("arena", "mutator")
    assert step["pull"].title == "pull the sandbox images"
    assert plan.yours == () and plan.notes == ()


def test_linux_prints_the_runtime_install_and_plans_no_pull():
    plan = build_plan(sit(facts=UBUNTU, runtime=NONE,
                          checks=checks(container_runtime="fail", arena_image="fail",
                                        mutator_image="fail")), linux)
    assert ids(plan) == []
    assert [t.does for t in plan.yours] == ["install Docker"]


def test_linux_gh_missing_is_printed_and_its_login_waits_for_the_next_run():
    plan = build_plan(sit(facts=UBUNTU, checks=checks(gh="fail"), gh_state="missing",
                          gh_login=None), linux)
    assert ids(plan) == []
    assert plan.yours[0].say == ("install the GitHub CLI: `sudo apt install gh` (or see "
                                 "https://github.com/cli/cli/blob/trunk/docs/install_linux.md)")


def test_gh_installed_but_logged_out_gets_only_the_login_and_the_account_check():
    plan = build_plan(sit(checks=checks(gh="fail"), gh_state="unauthed", gh_login=None), macos)
    assert ids(plan) == ["gh-login", "same-account"]
    assert plan.steps[0].needs == () and plan.steps[1].needs == ("gh-login",)


def test_two_accounts_already_logged_in_is_a_todo_not_a_step():
    plan = build_plan(sit(hub_login="alice", gh_login="bob"), macos)
    assert ids(plan) == []
    assert "gh is @bob but the hub login is @alice" in plan.yours[0].say


def test_the_pull_shows_its_size_as_an_upper_bound():
    # The registry's size counts every layer; after a re-pin most of them are
    # already here, so the real download is smaller -- never larger.
    plan = build_plan(sit(checks=checks(mutator_image="warn"), pull_size=432_400_000), macos)
    (pull,) = plan.steps
    assert pull.title == "pull the mutator image" and pull.shows == "up to 432 MB"


def test_an_unknown_pull_size_shows_nothing_extra():
    plan = build_plan(sit(checks=checks(arena_image="warn")), macos)
    assert plan.steps[0].shows == ""


def test_scope_eval_plans_only_what_eval_needs():
    plan = build_plan(sit(scope="eval", checks=checks(hub_login="fail", gh="fail",
                                                      arena_image="warn"),
                          gh_state="missing", hub_login=None, agent=None), macos)
    assert ids(plan) == ["pull"] and plan.steps[0].images == ("arena",)
    assert plan.notes == ()


def test_no_agent_chosen_leaves_a_note_instead_of_a_step():
    plan = build_plan(sit(agent=None, agent_installed=False, agent_logged_in=False), macos)
    assert ids(plan) == [] and "--operator" in plan.notes[0]


def test_opencode_needs_no_install_and_no_login():
    assert build_plan(sit(agent="opencode2"), macos).empty


def test_an_installed_agent_that_is_logged_out_gets_only_its_login():
    plan = build_plan(sit(agent="codex", agent_logged_in=False), macos)
    assert ids(plan) == ["agent-login"] and plan.steps[0].argv == ("codex", "login")


def test_rosetta_advice_goes_after_the_run():
    plan = build_plan(sit(checks=checks(rosetta="warn"), emulation=macos.ROSETTA_DOCKER_DESKTOP),
                      macos)
    assert ids(plan) == []
    assert plan.afterwards[0].say == macos.ROSETTA_DOCKER_DESKTOP.say


def test_a_fresh_mac_without_rosetta_installs_colima_and_holds_the_vm_and_the_pull():
    # Starting a Rosetta VM without Rosetta 2 pops Apple's dialog or fails
    # mid-run, after the person was told they could walk away.
    plan = build_plan(sit(facts=replace(MAC, host_rosetta=False), runtime=NONE,
                          emulation=macos.ROSETTA_INSTALL,
                          checks=checks(container_runtime="fail", arena_image="fail",
                                        mutator_image="fail", rosetta="warn")), macos)
    assert ids(plan) == ["runtime-1"] and plan.steps[0].recipe is macos.COLIMA_INSTALL
    assert [t.say for t in plan.yours] == [macos.ROSETTA_INSTALL.say]
    assert plan.afterwards == ()      # said once, before the run, not again after it


def test_listed_commands_for_someone_without_a_terminal():
    plan = build_plan(sit(checks=checks(hub_login="fail", gh="fail"), hub_login=None,
                          gh_login=None, gh_state="unauthed"), macos)
    step = {s.id: s for s in plan.steps}
    assert listed_command(step["hub-login"]) == "nethackers login"
    # The plan's row names the same command an agent would run.
    assert step["hub-login"].shows == "nethackers login (a GitHub code, in your browser)"
    assert listed_command(step["gh-login"]) == (
        "gh auth login --hostname github.com --git-protocol https --web")
