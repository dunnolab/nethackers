"""Local storage of the user's GitHub identity and tokens at
~/.nethackers/credentials.json (chmod 600), plus token→login resolution
mirroring hub.auth.GitHubAppAuth. Injectable http for tests.

The stored credential carries the full token set (access + refresh + expiry)
so the CLI can refresh silently; ``load`` migrates the old ``{login, token}``
files by mapping ``token`` onto ``access_token``."""
from __future__ import annotations

import contextlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import httpx


@dataclass(frozen=True)
class Credentials:
    login: str
    access_token: str
    refresh_token: str | None = None
    expires_at: float | None = None

    def is_expired(self, now: float) -> bool:
        return self.expires_at is not None and now >= self.expires_at - 60


def path() -> Path:
    return Path.home() / ".nethackers" / "credentials.json"


def load() -> Credentials | None:
    try:
        data = json.loads(path().read_text())
    except (OSError, ValueError):
        return None
    try:
        if "access_token" in data:  # new format
            return Credentials(
                login=str(data["login"]),
                access_token=str(data["access_token"]),
                refresh_token=(str(data["refresh_token"]) if data.get("refresh_token") else None),
                expires_at=(
                    float(data["expires_at"]) if data.get("expires_at") is not None else None
                ),
            )
        return Credentials(login=str(data["login"]), access_token=str(data["token"]))  # migrate old
    except (KeyError, TypeError, ValueError):
        return None


def save(creds: Credentials) -> None:
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(asdict(creds), indent=2))
    os.chmod(p, 0o600)


def clear() -> None:
    with contextlib.suppress(FileNotFoundError):
        path().unlink()


def whoami_from_token(token: str, http: Any = httpx) -> str:
    resp = http.get(
        "https://api.github.com/user",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        },
    )
    if resp.status_code != 200:
        raise ValueError(f"github token validation failed: {resp.status_code}")
    return str(resp.json()["login"])
