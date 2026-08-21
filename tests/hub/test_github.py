# tests/hub/test_github.py
import pytest

from nethackers.hub.github import GitHubRead, GitHubReadError, parse_repo


@pytest.mark.parametrize("repo,exp", [
    ("sam/nethacker", ("sam", "nethacker")),
    ("github.com/sam/nethacker", ("sam", "nethacker")),
    ("https://github.com/sam/nethacker.git", ("sam", "nethacker")),
])
def test_parse_repo(repo, exp): assert parse_repo(repo) == exp


def test_parse_repo_bad():
    with pytest.raises(ValueError): parse_repo("nethacker")


class _Resp:
    def __init__(self, code): self.status_code = code


def test_commit_exists_true():
    class H:
        def get(self, url, headers=None):
            assert url == "https://api.github.com/repos/sam/nethacker/commits/" + "a"*40
            assert headers["Authorization"] == "Bearer ghu_x"
            return _Resp(200)
    assert GitHubRead("ghu_x", http=H()).commit_exists("github.com/sam/nethacker", "a"*40) is True


def test_commit_exists_false_on_404():
    class H:
        def get(self, url, headers=None): return _Resp(404)
    assert GitHubRead("ghu_x", http=H()).commit_exists("sam/nethacker", "a"*40) is False


def test_commit_exists_raises_on_500():
    class H:
        def get(self, url, headers=None): return _Resp(500)
    with pytest.raises(GitHubReadError):
        GitHubRead("ghu_x", http=H()).commit_exists("sam/nethacker", "a"*40)
