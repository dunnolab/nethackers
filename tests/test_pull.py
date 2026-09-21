"""Exercises ``nethackers.hubclient.pull.pull``'s clone+checkout command
construction with a fake git runner -- no real ``git`` process and no
network access are involved.
"""

import pytest

from nethackers.github_ref import NonGitHubRef
from nethackers.hubclient.pull import pull


def test_pull_builds_clone_and_checkout(tmp_path):
    cmds = []

    dest = pull(
        "dunnolab/nethacker@abc123", tmp_path / "d", runner=lambda c, check: cmds.append(c)
    )

    assert "clone" in cmds[0]
    assert "--recurse-submodules" not in cmds[0]
    assert "protocol.allow=never" in cmds[0]
    assert "core.symlinks=false" in cmds[0]
    assert cmds[0][-2].endswith("dunnolab/nethacker.git")
    assert cmds[1][-1] == "abc123"
    assert dest == tmp_path / "d"


def test_pull_accepts_github_prefixed_repo(tmp_path):
    # the hub stores repos host-qualified ("github.com/owner/name"); pulling that
    # must NOT double-prefix into https://github.com/github.com/...
    cmds = []
    pull("github.com/vkurenkov/nethacker@abc123", tmp_path / "d",
         runner=lambda c, check: cmds.append(c))
    assert cmds[0][-2] == "https://github.com/vkurenkov/nethacker.git"


def test_pull_accepts_full_url(tmp_path):
    cmds = []
    pull("https://github.com/o/r@abc123", tmp_path / "d",
         runner=lambda c, check: cmds.append(c))
    assert cmds[0][-2] == "https://github.com/o/r.git"


def test_pull_rejects_missing_commit(tmp_path):
    with pytest.raises(ValueError):
        pull("dunnolab/nethacker", tmp_path / "d", runner=lambda c, check: None)


def test_pull_rejects_non_github(tmp_path):
    with pytest.raises(NonGitHubRef):
        pull("https://evil.example/sam/x@" + "a" * 40, tmp_path, runner=lambda *a, **k: None)


def test_pull_clone_is_hardened(tmp_path):
    calls = []

    def runner(cmd, **k):
        calls.append(cmd)

    pull("github.com/sam/nethacker@" + "a" * 40, tmp_path, runner=runner)
    clone = calls[0]
    assert "--recurse-submodules" not in clone
    assert "-c" in clone and "protocol.allow=never" in clone
    assert "core.symlinks=false" in clone
    assert clone[:2] == ["git", "clone"] or "clone" in clone
