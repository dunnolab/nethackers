"""Local storage of the user's GitHub identity ({login, token}) at
~/.nethackers/credentials.json (chmod 600), plus token→login resolution
mirroring hub.auth.GitHubAppAuth. Injectable http for tests."""
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
    token: str


def path() -> Path:
    return Path.home() / ".nethackers" / "credentials.json"


def load() -> Credentials | None:
    p = path()
    try:
        data = json.loads(p.read_text())
        return Credentials(login=str(data["login"]), token=str(data["token"]))
    except (OSError, ValueError, KeyError, TypeError):
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
