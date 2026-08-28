"""The stage/environment config model. One frozen dataclass whose field
defaults ARE the prod stage; loaded through defaults < .env.stack file
(Task 5) < NETHACKERS_* env < CLI flag. No secret is ever a field."""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, fields
from pathlib import Path

# Client-side stub-identity fallbacks, used when no one is logged in (the
# offline stub hub's identity). A protocol pair with compose's stub map, NOT
# per-stage values -- see the spec's Core model / Identity display.
OFFLINE_TOKEN = "offline-token"
OFFLINE_OWNER = "offline"


@dataclass(frozen=True)
class Stage:
    name: str = "prod"  # display id: "prod" or the worktree slug
    hub_url: str = "https://nethackers.dunnolab.ai"  # S1/S2 -> kills K7
    hub_port: int = 8000  # S3/S18 (compose/make read it)
    compose_project: str = "nethackers"  # S17 (compose reads it)
    data_root: Path = Path.home() / ".nethackers" / "evolve"  # S5/S6 -> kills K1
    repo_name: str = "nethacker"  # S7 -> kills K6
    arena_image: str = "nethackers/arena:dev"  # S14 -> kills K5
    mutator_image: str = "nethackers/mutator:latest"  # S15 -> kills K5
    github_client_id: str = "Iv23liWooDi2WlkrDAOw"  # S13 (public App id -- config, not secret)

    @property
    def runs_dir(self) -> Path:
        return self.data_root / "runs"

    @property
    def store_dir(self) -> Path:
        return self.data_root / "store"


# NETHACKERS_* env var -> Stage field name. COMPOSE_PROJECT_NAME is the one
# non-namespaced key (compose's own convention).
_ENV_TO_FIELD = {
    "NETHACKERS_STAGE": "name",
    "NETHACKERS_HUB": "hub_url",
    "NETHACKERS_HUB_PORT": "hub_port",
    "COMPOSE_PROJECT_NAME": "compose_project",
    "NETHACKERS_DATA_ROOT": "data_root",
    "NETHACKERS_REPO_NAME": "repo_name",
    "NETHACKERS_ARENA_IMAGE": "arena_image",
    "NETHACKERS_MUTATOR_IMAGE": "mutator_image",
    "NETHACKERS_CLIENT_ID": "github_client_id",
}


def _coerce(field_name: str, raw: str) -> object:
    if field_name == "hub_port":
        return int(raw)
    if field_name == "data_root":
        return Path(raw)
    return raw


def _from_env(environ: Mapping[str, str]) -> dict[str, object]:
    out: dict[str, object] = {}
    for env_key, field_name in _ENV_TO_FIELD.items():
        if env_key in environ:
            out[field_name] = _coerce(field_name, environ[env_key])
    return out


def _find_stack_file(cwd: Path | None, environ: Mapping[str, str]) -> Path | None:
    """Locate the ``.env.stack`` file layer. ``NETHACKERS_STAGE_FILE`` is an
    explicit hatch: a path targets exactly that file, an empty string skips
    discovery altogether (forced prod -- ``--prod``'s mechanism in cli.py).
    Otherwise walk up from ``cwd`` (or the real process cwd) looking for
    ``.env.stack``, the same file ``scripts/stack.py`` writes at a
    worktree's root."""
    override = environ.get("NETHACKERS_STAGE_FILE")
    if override is not None:                    # explicit hatch: path targets it, "" ignores
        return Path(override) if override else None
    here = (cwd or Path.cwd()).resolve()
    for d in (here, *here.parents):
        f = d / ".env.stack"
        if f.is_file():
            return f
    return None


def _parse_env_file(path: Path | None) -> dict[str, object]:
    """Parse a flat ``KEY=VALUE`` ``.env.stack`` file into Stage field
    values, via the same ``_ENV_TO_FIELD`` map + ``_coerce`` the process-env
    layer uses. A missing/absent path or an unknown key is silently
    ignored -- this layer, like every layer above defaults, is optional."""
    if path is None or not path.is_file():
        return {}
    out: dict[str, object] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            field = _ENV_TO_FIELD.get(k.strip())
            if field:
                out[field] = _coerce(field, v.strip())
    return out


def load_stage(cwd: Path | None = None, environ: Mapping[str, str] = os.environ) -> Stage:
    """Resolve the active Stage: prod defaults < .env.stack file < process
    env. CLI flags are the 4th layer and win last -- they are applied by
    argparse using this Stage's fields as its defaults, in cli.py."""
    values: dict[str, object] = {f.name: getattr(Stage(), f.name) for f in fields(Stage)}
    values.update(_parse_env_file(_find_stack_file(cwd, environ)))
    values.update(_from_env(environ))
    return Stage(**values)  # type: ignore[arg-type]
