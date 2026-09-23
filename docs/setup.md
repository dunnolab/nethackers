# Setting up a machine

`nethackers setup` gets a machine ready to evaluate, evolve and publish bots:
the container runtime, the sandbox images, and three logins. macOS or Linux,
Python 3.11+; on Windows, run it inside WSL2. Recipes as of v0.35.0
(2026-09-23); what a bot is and how it is scored is in
[harness.md](harness.md).

## Run it

```bash
nethackers setup                    # checks the machine, shows a plan, asks once
nethackers setup --for eval         # only what evaluating needs (also: evolve, publish, browse)
nethackers setup --operator codex   # the coding agent evolve should use
nethackers setup --yes              # run the plan without asking
```

It runs the same checks as `nethackers doctor`, prints what it is about to
do, and waits for one yes. Nothing it runs uses `sudo`; a test checks every
command. Anything that needs `sudo`, a GUI click, or logging out and back in
is printed for you to run.

## The plan

On a Mac with Homebrew and nothing else installed, `--operator codex` plans
this:

```text
nethackers will:
  1 log you in to the hub                nethackers login (a GitHub code, in your browser)
  2 install the GitHub CLI               brew install gh  (untested)
  3 log you in to gh                     gh auth login --hostname github.com --git-protocol https --web
  4 check gh and the hub are one account compares the two GitHub logins
  5 install Codex                        curl -fsSL https://chatgpt.com/codex/install.sh | sh  (untested)
  6 log you in to Codex                  codex login
  7 install Colima + Docker              brew install colima docker  (untested)
  8 start Colima with Rosetta            colima start --vm-type vz --vz-rosetta --cpu 6 --memory 12  (untested)
  9 pull the sandbox images
Steps 1, 3, 6 need you at the keyboard; the rest run on their own. Nothing nethackers
runs needs sudo.
Steps marked (untested) come from vendor docs and haven't been run on a real macOS arm64
(Apple Silicon) yet.
```

Logins come first so you can walk away afterwards. Installs happen only
where one documented command does them without `sudo`. A runtime is started
with its own command: `colima start`, `docker desktop start`, `orb start`,
`podman machine start`. The images are about 1 GB to download the first
time and 4 GB on disk; later releases fetch only what changed, with a
progress bar and the time left.

On Linux the runtime and `gh` need `sudo`, so they move to the list you run
yourself:

```text
You'll need to (nethackers never runs sudo):
  • install the GitHub CLI: `sudo apt install gh` (or see
    https://github.com/cli/cli/blob/trunk/docs/install_linux.md)  (untested)
  • install Docker: `curl -fsSL https://get.docker.com | sudo sh`, then `sudo usermod
    -aG docker $USER` and log out and back in  (untested)
```

## Check it worked

```bash
nethackers doctor
```

Each section ends in `ready to eval: yes`, `ready to evolve: yes`,
`ready to publish: yes`. On Linux the new `docker` group applies only after
you log out and back in; until then doctor reports the runtime as installed
but broken.

## Running it again

Run it whenever you like: it re-checks and plans only what is still missing,
which is also how a new release's images arrive. With no terminal attached
(a coding agent's shell, a pipe) it prints the plan and changes nothing;
with `--yes` it runs every unattended step and lists the logins for you to
run, each of which prints a code or a link.

## Coding agents

`evolve` needs one agent logged in on this machine; the sandbox reuses that
login.

| | log in with | what enters the sandbox |
|---|---|---|
| Claude Code | run `claude` once | the credential: `~/.claude/.credentials.json` read-only on Linux, the Keychain OAuth token as an environment variable on macOS |
| Codex | `codex login` | your real `~/.codex`, read-write, because its tokens rotate |
| OpenCode 2 | nothing; providers come from `~/.config/opencode/opencode.json` | a read-only copy of that file's `provider` section, plus the environment variables it names |

OpenCode 2 is provider-agnostic, so a few things differ:

- Logins made with `opencode2 auth login`, a ChatGPT subscription included,
  stay on the host: the sandbox never sees OpenCode's own database, and a
  subscription login renews itself, so a copy would invalidate yours. For
  GPT on a ChatGPT subscription, use the `codex` operator.
- A `{file:...}` key is not in the container. Use `{env:NAME}` or a literal
  `apiKey`, and export the variable in the shell that launches nethackers.
- Project config (`opencode.json`, `.opencode/`) is switched off in the
  sandbox: the worktree is a copy of someone else's program, and OpenCode
  trusts project config completely.
- Without a key, OpenCode serves a handful of free `opencode/*` models, and
  doctor says "free models only". Their availability is OpenCode's to
  decide; a model that never replies waits out the 8-hour sandbox timeout.
- Custom providers appear in the model picker as `provider/model`. A model
  server on your own machine is `http://host.docker.internal:PORT/v1` under
  Docker Desktop, since `localhost` inside the sandbox is the container.
- Reasoning effort is a variant of a pinned model, so `--effort` needs
  `--model`.

## What has been run on real machines

Every recipe below is one of:

- **tested**: someone ran `nethackers setup` through it on a real machine;
  the row says where, at which version, and when.
- **untested**: written from the linked vendor document, never run by us.
- **not covered**: setup points you at the vendor's page.

A recipe becomes tested only in a PR that records where it ran. As of
v0.35.0: macOS, 17 recipes, 0 tested; Linux, 13 recipes, 0 tested, 2 not
covered.

<!-- setup-recipes:start (generated by `python -m nethackers.setup.docs`; do not edit by hand) -->

### macOS

| Recipe | What it does | Runs it | Command or instruction | Status |
|---|---|---|---|---|
| `macos.colima.install` | install Colima + Docker | nethackers | `brew install colima docker` | untested ([built from](https://github.com/abiosoft/colima#readme)) |
| `macos.colima.start-new` | start Colima with Rosetta | nethackers | `colima start --vm-type vz --vz-rosetta --cpu 6 --memory 12` | untested ([built from](https://github.com/abiosoft/colima/blob/main/cmd/start.go)) |
| `macos.colima.start` | start Colima | nethackers | `colima start` | untested ([built from](https://github.com/abiosoft/colima#readme)) |
| `macos.docker-desktop.start` | start Docker Desktop | nethackers | `docker desktop start` | untested ([built from](https://docs.docker.com/reference/cli/docker/desktop/start/)) |
| `macos.docker-desktop.open` | start Docker Desktop | you | open Docker Desktop and wait until it says the engine is running | untested ([built from](https://docs.docker.com/desktop/setup/install/mac-install/)) |
| `macos.orbstack.start` | start OrbStack | nethackers | `orb start` | untested ([built from](https://docs.orbstack.dev/headless)) |
| `macos.podman.start` | start the Podman machine | nethackers | `podman machine start` | untested ([built from](https://docs.podman.io/en/latest/markdown/podman-machine-start.1.html)) |
| `macos.podman.init` | create a Podman machine | you | create and start a Podman machine: `podman machine init && podman machine start` | untested ([built from](https://docs.podman.io/en/latest/markdown/podman-machine-init.1.html)) |
| `macos.runtime.no-homebrew` | install a container runtime | you | install Homebrew (https://brew.sh; its installer uses sudo), or install Docker Desktop (https://docs.docker.com/desktop/setup/install/mac-install/) or OrbStack (https://orbstack.dev) yourself | untested ([built from](https://brew.sh)) |
| `macos.gh.install` | install the GitHub CLI | nethackers | `brew install gh` | untested ([built from](https://github.com/cli/cli#installation)) |
| `macos.gh.no-homebrew` | install the GitHub CLI | you | install the GitHub CLI from https://cli.github.com | untested ([built from](https://github.com/cli/cli#installation)) |
| `macos.claude.install` | install Claude Code | nethackers | `curl -fsSL https://claude.ai/install.sh \| bash` | untested ([built from](https://code.claude.com/docs/en/setup)) |
| `macos.codex.install` | install Codex | nethackers | `curl -fsSL https://chatgpt.com/codex/install.sh \| sh` | untested ([built from](https://github.com/openai/codex)) |
| `macos.rosetta.install` | install Rosetta | you | install Rosetta: `softwareupdate --install-rosetta --agree-to-license` | untested ([built from](https://support.apple.com/en-us/102527)) |
| `macos.docker-desktop.rosetta` | turn on Rosetta in Docker Desktop | you | turn on Rosetta: Docker Desktop → Settings → General → "Apple Virtualization framework" and "Use Rosetta for x86_64/amd64 emulation" (Docker Desktop restarts) | untested ([built from](https://docs.docker.com/desktop/settings-and-maintenance/settings/)) |
| `macos.colima.rosetta` | recreate Colima with Rosetta | you | recreate Colima with Rosetta: `colima delete` (this deletes its images and containers), then run `nethackers setup` again | untested ([built from](https://github.com/abiosoft/colima/blob/main/cmd/start.go)) |
| `macos.podman.no-rosetta` | use a runtime with Rosetta | you | Podman runs amd64 under QEMU on Apple Silicon; for faster evaluation use Colima (`brew install colima docker`, then `nethackers setup`) or OrbStack | untested ([built from](https://github.com/containers/podman/blob/main/RELEASE_NOTES.md)) |

### Linux

| Recipe | What it does | Runs it | Command or instruction | Status |
|---|---|---|---|---|
| `linux.docker.install` | install Docker | you | install Docker: `curl -fsSL https://get.docker.com \| sudo sh`, then `sudo usermod -aG docker $USER` and log out and back in | untested ([built from](https://docs.docker.com/engine/install/ubuntu/#install-using-the-convenience-script)) |
| `linux.docker.install-arch` | install Docker | you | install Docker: `sudo pacman -S docker && sudo systemctl enable --now docker`, then `sudo usermod -aG docker $USER` and log out and back in | untested ([built from](https://wiki.archlinux.org/title/Docker)) |
| `linux.docker.install-other` | install Docker | you | install Docker for your distribution: https://docs.docker.com/engine/install/ | not covered ([vendor page](https://docs.docker.com/engine/install/)) |
| `linux.docker.wsl` | get Docker working in WSL | you | install Docker Desktop for Windows and turn on its WSL integration for this distro (https://docs.docker.com/desktop/features/wsl/), or install Docker Engine inside WSL: `curl -fsSL https://get.docker.com \| sudo sh` | untested ([built from](https://docs.docker.com/desktop/features/wsl/)) |
| `linux.docker.start` | start Docker | you | start Docker: `sudo systemctl enable --now docker` | untested ([built from](https://docs.docker.com/engine/install/linux-postinstall/)) |
| `linux.docker.group` | let your user reach Docker | you | let your user reach Docker: `sudo usermod -aG docker $USER`, then log out and back in (or run `newgrp docker` in this shell) | untested ([built from](https://docs.docker.com/engine/install/linux-postinstall/)) |
| `linux.podman.fix` | fix Podman | you | fix Podman (its error is shown above); rootless Podman needs subuid/subgid ranges: https://github.com/containers/podman/blob/main/docs/tutorials/rootless_tutorial.md | untested ([built from](https://github.com/containers/podman/blob/main/docs/tutorials/rootless_tutorial.md)) |
| `linux.gh.apt` | install the GitHub CLI | you | install the GitHub CLI: `sudo apt install gh` (or see https://github.com/cli/cli/blob/trunk/docs/install_linux.md) | untested ([built from](https://github.com/cli/cli/blob/trunk/docs/install_linux.md)) |
| `linux.gh.dnf` | install the GitHub CLI | you | install the GitHub CLI: `sudo dnf install gh` (or see https://github.com/cli/cli/blob/trunk/docs/install_linux.md) | untested ([built from](https://github.com/cli/cli/blob/trunk/docs/install_linux.md)) |
| `linux.gh.pacman` | install the GitHub CLI | you | install the GitHub CLI: `sudo pacman -S github-cli` (or see https://github.com/cli/cli/blob/trunk/docs/install_linux.md) | untested ([built from](https://github.com/cli/cli/blob/trunk/docs/install_linux.md)) |
| `linux.gh.other` | install the GitHub CLI | you | install the GitHub CLI: https://github.com/cli/cli/blob/trunk/docs/install_linux.md | not covered ([vendor page](https://github.com/cli/cli/blob/trunk/docs/install_linux.md)) |
| `linux.claude.install` | install Claude Code | nethackers | `curl -fsSL https://claude.ai/install.sh \| bash` | untested ([built from](https://code.claude.com/docs/en/setup)) |
| `linux.codex.install` | install Codex | nethackers | `curl -fsSL https://chatgpt.com/codex/install.sh \| sh` | untested ([built from](https://github.com/openai/codex)) |

<!-- setup-recipes:end -->

Native Windows is not covered: run nethackers inside WSL2. Other Linux
distributions: Docker's own install page, linked in the table.
