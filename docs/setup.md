# Setting up a machine

`nethackers setup` gets a machine ready to evaluate, evolve and publish bots:
the container runtime, the sandbox images, and three logins. macOS or Linux,
Python 3.11+; on Windows, run it inside WSL2. Recipes as of v0.37.0
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
do, and waits for one yes; with no coding agent logged in and no
`--operator`, it first asks which one to set up. Nothing it runs uses
`sudo`; a test checks every recipe and login. Anything that needs `sudo`, a
GUI click, or logging out and back in is printed for you to run.

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
 10 install codex's TLS-impersonation helper (curl_cffi) uv pip install --python <this python> curl_cffi
Steps 1, 3, 6 need you at the keyboard; the rest run on their own. Nothing nethackers
runs needs sudo.
Steps marked (untested) come from vendor docs and haven't been run on a real Mac yet.
```

Logins come first so you can walk away afterwards. Installs happen only
where one documented command does them without `sudo`. A runtime is started
with its own command: `colima start`, `docker desktop start`, `orb start`,
`podman machine start`; step 8 is sized to the Mac, at most 6 CPUs and 12 GB
and never more than half its memory, and a Mac without Rosetta 2 is told to
install it first and gets the VM on the next run. The images are up to about
1 GB to download the first time (the plan shows the exact figure) and a few
GB on disk; later releases fetch only what changed, with a progress bar and
the time left. Step 10 appears for the `codex` operator only: its credential
broker forwards through `curl_cffi`, which is installed into nethackers' own
interpreter, never into the sandbox, and the broker installs it itself on
first use if setup was skipped.

On Linux the runtime and `gh` need `sudo`, so they move to the list you run
yourself, and setup ends with "Then run `nethackers setup` again. It picks
up where it left off": the second run pulls the images and plans the `gh`
login. On a host with `ufw`, the list also carries the one firewall rule the
credential broker needs:

```text
You'll need to (nethackers never runs sudo):
  • install the GitHub CLI: `sudo apt install gh` (or see
    https://github.com/cli/cli/blob/trunk/docs/install_linux.md)  (untested)
  • install Docker: `curl -fsSL https://get.docker.com | sudo sh`, then `sudo usermod
    -aG docker $USER` and log out and back in  (untested)
  • if ufw is active, allow the sandboxed agent to reach the credential broker (on by
    default) with a PORT-SCOPED rule (not a blanket `allow in on docker0`): `sudo ufw
    allow in on docker0 to "$(docker network inspect bridge -f '{{(index .IPAM.Config
    0).Gateway}}')" port 11700:11749 proto tcp`
```

## Check it worked

```bash
nethackers doctor
```

Each section ends in a verdict, `ready to eval ✓` (`ready to eval: yes` with
`-o plain`), and the same for evolve, publish and browse. A finished setup
says so itself: `✓ ready to eval · evolve · publish · browse. Nothing to
do.`, then a `Next:` line with the command to try. On Linux the new `docker`
group applies only after you log out and back in, or `newgrp docker` in that
shell; until then doctor's `container_runtime` check fails with docker's
permission-denied line.

## Running it again

Run it whenever you like: it re-checks and plans only what is still missing,
which is also how a new release's images arrive. With no terminal attached
(a coding agent's shell, a pipe) it prints the plan and changes nothing;
with `--yes` it runs every unattended step and lists the logins for you to
run, each of which prints a code or a link. Pass `--operator` with `--yes`,
or an unattended run leaves the coding agent for later.

## Coding agents

`evolve` needs one agent logged in on this machine. The login stays on the
host: by default a credential broker on the host injects it on the wire, and
the sandbox only ever sees a placeholder and the broker's address
([harness.md](harness.md#the-coding-agent)). `--no-broker`, or the form's
Credential toggle, mounts the credential instead.

| | log in with | what the sandbox sees by default | what `--no-broker` mounts |
|---|---|---|---|
| Claude Code | `claude auth login` | a placeholder token and a base URL to the broker | `~/.claude/.credentials.json` read-only on Linux; the Keychain OAuth token as `CLAUDE_CODE_OAUTH_TOKEN` on macOS |
| Codex | `codex login` | a provider override in its command pointing at the broker | your real `~/.codex`, read-write, because its tokens rotate |
| OpenCode 2 | nothing; providers come from `~/.config/opencode/opencode.json` (or `.jsonc`) | a copy of the `provider` section with each brokerable key replaced by a placeholder and the broker's URL | the copy with the keys, plus the environment variables it names |

The operator id is `opencode2`; the CLI inside the image is `opencode`,
with an `opencode2` symlink. OpenCode is provider-agnostic, so a few things
differ:

- Logins made with `opencode auth login`, a ChatGPT subscription included,
  stay on the host: the sandbox never sees OpenCode's own database. For GPT
  on a ChatGPT subscription, use the `codex` operator.
- A `{file:...}` key is not in the container. Use `{env:NAME}` or a literal
  `apiKey`, and export the variable in the shell that launches nethackers.
- Project config (`opencode.json`, `.opencode/`) is switched off in the
  sandbox: the worktree is a copy of someone else's program, and OpenCode
  trusts project config completely.
- Without a key, OpenCode serves a handful of free `opencode/*` models, and
  doctor says "free models only". Their availability is OpenCode's to
  decide; a model that never replies waits out the 8-hour sandbox timeout.
- Custom providers appear in the model picker as `provider/model`. Under
  Docker Desktop a model server on your own machine is
  `http://host.docker.internal:PORT/v1`, since `localhost` inside the
  sandbox is the container; on Linux Docker the sandbox has no name for the
  host at all.
- Reasoning effort is a variant of a pinned model, so `--effort` needs
  `--model`.

## What has been run on real machines

Every recipe below is one of:

- **tested**: someone ran `nethackers setup` through it on a real machine;
  the row says where, at which version, and when.
- **untested**: written from the linked vendor document, never run by us.
- **not covered**: setup points you at the vendor's page.

A recipe becomes tested only in a PR that records where it ran, at which
nethackers version, and when; the tests reject a tested row missing any of
the three, and fail while this table is stale. As of v0.37.0: macOS, 17
recipes, 0 tested; Linux, 14 recipes, 1 tested (the broker's firewall rule,
on Ubuntu), 2 not covered.

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
| `linux.broker.ufw` | let the sandbox reach the credential broker (ufw) | you | if ufw is active, allow the sandboxed agent to reach the credential broker (on by default) with a PORT-SCOPED rule (not a blanket `allow in on docker0`): `sudo ufw allow in on docker0 to "$(docker network inspect bridge -f '{{(index .IPAM.Config 0).Gateway}}')" port 11700:11749 proto tcp` | tested on Ubuntu 26.04 x86_64, Docker 29.1.3 (nethackers 0.35.0, 2026-09-23) |

<!-- setup-recipes:end -->

Native Windows is not covered: setup says so, points at Microsoft's WSL2
page, and exits. Inside WSL2 it treats the machine as Linux and offers the
`linux.docker.wsl` row. Other Linux distributions, meaning any whose
`/etc/os-release` names none of Debian, Ubuntu, Fedora, RHEL, CentOS or
Arch: setup prints Docker's and gh's own install pages, the two rows marked
not covered. The Claude Code and Codex installers run on any distribution.
