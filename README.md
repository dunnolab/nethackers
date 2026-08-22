# nethackers

Solve NetHack by evolving symbolic players. This is the evaluation-and-registration core: from one CLI you log in with GitHub, publish a solution to your own public `nethacker` repo, and register it with the shared hub at `https://nethackers.dunnolab.ai` (the default).

## Quickstart (contributors)

1. `pip install nethackers` — install the CLI from PyPI; that is all a contributor needs.
2. `nethackers login` — authenticate once with GitHub (device flow; token stored + refreshed silently). Also have `gh` installed and `gh auth login`'d as the **same** account — `submit` uses it to create/push your repo.
3. `nethackers submit ./solution` — publish your solution to your public `github.com/<you>/nethacker` repo (created if it doesn't exist) and register the resulting `repo@commit`. One command, no manual git.
4. `nethackers board` — browse the leaderboard.

Prefer to manage the repo yourself? `nethackers register --repo github.com/<you>/nethacker --commit <full-sha>` registers an existing pinned commit directly.

---

## Run your own hub (advanced / unsupported)

Contributors do not need this — every command above talks to the shared hub. If you still want a private one:

```bash
pip install 'nethackers[hub]'
nethackers-hub
```

The `[hub]` extra pulls in the server dependencies and `nethackers-hub` serves the API (default `0.0.0.0:8000`). For the hardened Docker + Caddy deployment, see [`deploy/README.md`](deploy/README.md).

This is supported but unadvised — we neither push you toward it nor prevent it; if you host it, you own it.

## Design

Rationale and full design: [`docs/superpowers/specs/2026-08-22-remote-hub-and-github-login-design.md`](docs/superpowers/specs/2026-08-22-remote-hub-and-github-login-design.md).
