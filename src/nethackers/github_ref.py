"""Canonicalize a registered/pulled repo reference to github.com/<owner>/<name>,
or reject it. Stdlib-only leaf — imported by both hub/ (server-side registration
validation) and hubclient/ (client-side pull), so it lives at the top level and
imports neither. Host is PARSED, never substring-matched: github.com@evil,
github.com.evil, evil/github.com, and the scp form all fail here."""
from __future__ import annotations

from urllib.parse import urlsplit


class NonGitHubRef(ValueError):
    """The repo reference is not an unambiguous github.com/<owner>/<name>."""


def normalize_github_ref(repo: str) -> str:
    raw = repo.strip()
    if not raw:
        raise NonGitHubRef("empty repo reference")
    # scp form (git@github.com:owner/name) and other schemes are refused outright.
    if "@" in raw.split("/", 1)[0] or raw.startswith(("git@", "ssh://", "git://", "file://", "ext::")):
        raise NonGitHubRef(f"unsupported reference form: {repo!r}")
    if raw.startswith(("http://", "https://")):
        parts = urlsplit(raw)
        if parts.username or parts.password or parts.port or parts.query or parts.fragment:
            raise NonGitHubRef(f"reference must be a bare github.com URL: {repo!r}")
        if parts.hostname != "github.com":
            raise NonGitHubRef(f"host is not github.com: {repo!r}")
        path = parts.path
    elif raw.startswith("github.com/"):
        path = "/" + raw[len("github.com/"):]
    elif "/" in raw and not raw.startswith("/"):
        path = "/" + raw            # bare owner/name
    else:
        raise NonGitHubRef(f"cannot parse github owner/name from {repo!r}")
    segs = [s for s in path.removesuffix(".git").strip("/").split("/") if s]
    if len(segs) != 2:
        raise NonGitHubRef(f"expected exactly owner/name, got {path!r}")
    owner, name = segs
    if not owner or not name:
        raise NonGitHubRef(f"empty owner or name in {repo!r}")
    return f"github.com/{owner}/{name}"
