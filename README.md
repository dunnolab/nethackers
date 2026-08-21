# nethackers

Solve NetHack by evolving symbolic players. This is the evaluation-and-registration core: from one CLI you log in with GitHub, register a solution as a public `repo@commit` link, and browse the hub. Registrations go to the shared hub at `https://nethackers.dunnolab.ai` by default.

## Quickstart (contributors)

1. `pip install nethackers` — install the CLI from PyPI; that is all a contributor needs.
2. `nethackers login` — authenticate once with GitHub via the device flow; the token is stored and refreshed silently.
3. `nethackers register --repo github.com/<you>/nethacker --commit <full-sha>` — register a public repo you own, pinned to a full 40-hex commit SHA.
4. `nethackers board` — show a leaderboard, solutions ranked on an objective.

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
