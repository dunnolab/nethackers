"""Exercises ``nethackers.hubclient.pull.pull``'s clone+checkout command
construction with a fake git runner -- no real ``git`` process and no
network access are involved.
"""

import pytest

from nethackers.hubclient.pull import pull


def test_pull_builds_clone_and_checkout(tmp_path):
    cmds = []

    dest = pull(
        "dunnolab/nethacker@abc123", tmp_path / "d", runner=lambda c, check: cmds.append(c)
    )

    assert cmds[0][:2] == ["git", "clone"]
    assert cmds[0][-2].endswith("dunnolab/nethacker.git")
    assert cmds[1][-1] == "abc123"
    assert dest == tmp_path / "d"


def test_pull_rejects_missing_commit(tmp_path):
    with pytest.raises(ValueError):
        pull("dunnolab/nethacker", tmp_path / "d", runner=lambda c, check: None)
