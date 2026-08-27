import os, subprocess, stat, textwrap
from pathlib import Path
import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "deploy" / "deploy-hub.sh"

def _write_exec(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

@pytest.fixture
def env(tmp_path):
    """Temp bin/ with stub docker+curl on PATH, temp state files, and a runner."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    # Generic stub: log the call, exit with an rc chosen by env var.
    _write_exec(bin_dir / "docker", textwrap.dedent(f"""\
        #!/usr/bin/env bash
        echo "docker $*" >> "{log}"
        case "$*" in
          login*)        cat >/dev/null; exit "${{RC_LOGIN:-0}}" ;;
          pull*)         exit "${{RC_PULL:-0}}" ;;
          run*)          exit "${{RC_RUN:-0}}" ;;
          *"ps -q"*)     echo "hubcid"; exit 0 ;;
          *" up "*)      exit "${{RC_UP:-0}}" ;;
          inspect*)      echo "${{INSPECT_OUT:-healthy}}"; exit "${{RC_INSPECT:-0}}" ;;
          *)             exit 0 ;;
        esac
    """))
    _write_exec(bin_dir / "curl", textwrap.dedent(f"""\
        #!/usr/bin/env bash
        echo "curl $*" >> "{log}"
        case "$*" in
          *127.0.0.1*|*localhost*) exit "${{RC_CURL_LOCAL:-0}}" ;;
          *)                       exit "${{RC_CURL_PUBLIC:-0}}" ;;
        esac
    """))
    state = tmp_path / "state"; state.mkdir()
    hub_env = state / "hub.env"
    hub_env.write_text("NETHACKERS_CLIENT_ID=pub\nNETHACKERS_HUB_IMAGE=ghcr.io/dunnolab/nethackers-hub@sha256:" + "a"*64 + "\n")
    history = state / "history.log"
    compose = state / "compose.yaml"; compose.write_text("services: {}\n")

    def run(args, *, stdin="", rc_env=None, ssh_original=None):
        e = dict(os.environ)
        e["PATH"] = f"{bin_dir}:{e['PATH']}"
        e.update({
            "HUB_ENV_FILE": str(hub_env), "DEPLOY_HISTORY": str(history),
            "COMPOSE_FILE": str(compose), "PUBLIC_HEALTH_URL": "https://nethackers.dunnolab.ai/healthz",
            "GHCR_USER": "ci", "HEALTH_RETRIES": "2", "HEALTH_INTERVAL": "0",
        })
        if ssh_original is not None:
            e["SSH_ORIGINAL_COMMAND"] = ssh_original
        if rc_env:
            e.update({k: str(v) for k, v in rc_env.items()})
        return subprocess.run(["bash", str(SCRIPT), *args], input=stdin,
                              capture_output=True, text=True, env=e)

    return type("Env", (), {"run": staticmethod(run), "hub_env": hub_env,
                            "history": history, "log": log,
                            "calls": staticmethod(lambda: log.read_text() if log.exists() else "")})()
