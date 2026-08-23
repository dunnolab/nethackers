# nethackers

Solve NetHack by evolving symbolic players. One CLI: log in with GitHub, evolve a
bot with a coding agent, and publish + register your wins to the shared hub at
`https://nethackers.dunnolab.ai` — where anyone can browse and fetch them.

## Install

```bash
pip install nethackers          # Python 3.11+   (or: uv tool install nethackers)
```

## Get started

```bash
nethackers login                # one-time GitHub sign-in (device flow, silent refresh)
nethackers board                # browse the leaderboard
```

## Evolve a bot

Improve a NetHack bot automatically with Claude Code or Codex. Needs Docker (the
sandboxed arena + mutator run there):

```bash
nethackers evolve --objective val-dwa-law-fem --operator codex
```

Every validated win is auto-published to your public `github.com/<you>/nethacker`
repo and registered with the hub. From any machine, fetch one back:

```bash
nethackers pull github.com/<you>/nethacker@<commit> ./fetched
```

## Publish an existing solution

Also have the GitHub CLI installed and `gh auth login`'d as the **same** account
(used to create/push your repo), then:

```bash
nethackers submit ./my-solution --objective val-dwa-law-fem
```

---

`nethackers --help` lists every command (`elites`, `frontier`, `search`, `show`,
`register`). The hub defaults to `https://nethackers.dunnolab.ai` (override with
`--hub` or `$NETHACKERS_HUB`). Self-hosting the hub and the full design:
[`deploy/README.md`](deploy/README.md) and [`docs/`](docs/).
