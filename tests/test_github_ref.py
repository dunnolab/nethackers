import pytest
from nethackers.github_ref import normalize_github_ref, NonGitHubRef

@pytest.mark.parametrize("good", [
    "github.com/sam/nethacker",
    "https://github.com/sam/nethacker",
    "https://github.com/sam/nethacker.git",
    "sam/nethacker",
])
def test_accepts_and_normalizes(good):
    assert normalize_github_ref(good) == "github.com/sam/nethacker"

@pytest.mark.parametrize("bad", [
    "https://evil.example/sam/nethacker",
    "https://github.com@evil.example/sam/x",     # userinfo — real host is evil
    "https://github.com.evil.example/sam/x",     # subdomain
    "https://evil.example/github.com/sam/x",     # path
    "git@github.com:sam/x",                      # scp form
    "github.com/sam/nethacker/extra",            # too many segments
    "github.com/sam",                            # too few
    "https://github.com:22/sam/x",               # port
    "https://github.com/sam/x?a=b",              # query
])
def test_rejects_non_github_and_bypasses(bad):
    with pytest.raises(NonGitHubRef):
        normalize_github_ref(bad)
