# src/nethackers/hub/github.py
from __future__ import annotations

from typing import Any

import httpx


class GitHubReadError(Exception): ...


def parse_repo(repo: str) -> tuple[str, str]:
    segments = repo.rstrip("/").removesuffix(".git").split("/")
    if len(segments) < 2 or not segments[-1] or not segments[-2]:
        raise ValueError(f"cannot parse owner/name from {repo!r}")
    return segments[-2], segments[-1]


class GitHubRead:
    def __init__(self, token: str, *, http: Any = httpx) -> None:
        self._token = token
        self._http = http

    def commit_exists(self, repo: str, sha: str) -> bool:
        owner, name = parse_repo(repo)
        resp = self._http.get(
            f"https://api.github.com/repos/{owner}/{name}/commits/{sha}",
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/vnd.github+json",
            },
        )
        if resp.status_code == 200:
            return True
        if resp.status_code in (404, 422):
            return False
        raise GitHubReadError(f"github commit lookup failed: {resp.status_code}")
