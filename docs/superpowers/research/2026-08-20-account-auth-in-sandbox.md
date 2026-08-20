# Subscription/OAuth auth for coding-agent CLIs inside per-run Docker containers

Research report for the mantaray evolutionary loop: container-per-run mutators, mac+Linux hosts, accident-grade threat model, **account/subscription auth (ChatGPT Plus/Pro, Claude Max), not API keys**. Facts are cited to primary sources; judgment calls are marked **[inference]**.

Date of research: 2026-08-20.

---

## 0. TL;DR matrix

| Harness | Sub auth works headless-in-container? | Credential store | Keychain? | Refresh writes back to file? | ToS status for sub auth |
|---|---|---|---|---|---|
| **Codex CLI** | **Yes — officially documented** (seed `auth.json`, `docker cp` example in official docs; device-code login) | `~/.codex/auth.json` (`$CODEX_HOME/auth.json`) | Optional, **off by default** (`cli_auth_credentials_store = "file"|"keyring"|"auto"`) | Yes; refresh at `last_refresh` ≳ ~8 days or on 401; **refresh tokens rotate** | First-party: fine. OpenAI runs an official "Sign in with ChatGPT" third-party program |
| **pi** | Yes — file-based creds, paste-redirect-URL login works without loopback callback; `--rpc` headless mode | `~/.pi/agent/auth.json` (plain JSON, `0600`) | **No** (no keytar/keyring) | Yes, auto-refresh on expiry into `auth.json` | ChatGPT/Copilot sub logins: OK. **Claude Pro/Max: no longer flat-rate — bills per-token "extra usage"** |
| **opencode** | Yes — file-based creds; device-code plugin for headless ChatGPT login | `~/.local/share/opencode/auth.json` | **No** (file-based) | Yes, automatic OAuth refresh | ChatGPT Plus/Pro OAuth is in the official docs. **Claude Pro/Max: server-side blocked Jan 2026, ToS-banned Feb 2026** |
| *(Claude Code — other agent's beat, sharp specifics only)* | Yes, first-party-only | macOS **Keychain** ("Claude Code-credentials"); Linux `~/.claude/.credentials.json`; `claude setup-token` → `CLAUDE_CODE_OAUTH_TOKEN` | **Yes on macOS** — the one harness with the Keychain problem | Yes | Sub auth allowed **only** in Claude Code / Claude.ai — third-party use explicitly banned |

Headline: **the file-based-credential + bind-mount pattern works for all three target harnesses**, is *officially documented* for Codex, and the single systemic hazard is **OpenAI refresh-token rotation** (never let two divergent copies of `auth.json` exist). The strategic constraint is that **Claude Max via pi/opencode is dead** (blocked + ToS-banned; falls back to per-token "extra usage" billing), so subscription-priced Anthropic inference is reachable only through Claude Code itself.

---

## 1. Per-harness details

### 1.1 Codex CLI (OpenAI)

**Auth modes.** Two: "Sign in with ChatGPT for subscription access" vs API key at metered rates ([learn.chatgpt.com/docs/auth](https://learn.chatgpt.com/docs/auth), formerly developers.openai.com/codex/auth).

**Storage.** Default `~/.codex/auth.json` (honors `$CODEX_HOME`). Contains `auth_mode`, `tokens {id_token, access_token, refresh_token}`, `last_refresh`. Docs: treat it "like a password: it contains access tokens." Credential backend is configurable via `cli_auth_credentials_store = "file" | "keyring" | "auto"` — **file is the effective default**, so macOS Keychain is *not* in the way (cited: official auth docs).

**Headless login options (all cited from official docs + issues #2798/#3820):**
1. `codex login --device-auth` — device-code flow (beta); punch a short code into a browser on any device. Works inside a container with no listener.
2. `ssh -L 1455:localhost:1455` — forward the fixed localhost callback port 1455 and run the normal browser flow.
3. **Copy `auth.json`** — officially blessed: "copy `$CODEX_HOME/auth.json` to the headless machine and codex should just work." The official docs literally include the container recipe: `docker exec MY_CONTAINER mkdir -p "$CONTAINER_HOME/.codex"` + `docker cp ~/.codex/auth.json MY_CONTAINER:"$CONTAINER_HOME/.codex/auth.json"`.

**The official CI/CD recipe (our use case, verbatim relevant):** [learn.chatgpt.com/docs/auth/ci-cd-auth](https://learn.chatgpt.com/docs/auth/ci-cd-auth) — "advanced workflow for enterprise trusted automation"; "API keys are still the recommended option for most CI/CD jobs," but account auth is supported:
- Seed `auth.json` once from a trusted machine (with `cli_auth_credentials_store = "file"`).
- Persistent runners: seed only if missing, **never overwrite per run**; let Codex refresh it in place.
- Ephemeral runners: restore `auth.json` before the job, **write the (possibly refreshed) file back afterward** in an `if: always()` step.
- Refresh triggers: `last_refresh` older than **~8 days**, or a 401. No manual refresh calls needed.
- **Concurrency rule (critical for us): "Use one `auth.json` per runner or per serialized workflow stream."** Do not share the file across concurrent jobs or use the same file on multiple machines simultaneously.

**The rotation gotcha (the thing that actually bites).** OpenAI OAuth refresh tokens are **rotating / single-use**: once a refresh token is used, stale copies are dead, and reusing one yields `refresh_token_reused` / 401 auth loops. Documented in the wild: openai/codex [#19803](https://github.com/openai/codex/issues/19803) ("Sign in to your OpenAI/ChatGPT account from a second device or browser session — this invalidates the refresh token stored locally in `~/.codex/auth.json`"; recovery = `rm ~/.codex/auth.json` + re-login), plus third-party routers hitting the same wall (cc-switch #4474, 9router #1444/#1663, multica #2081). Consequences for us:
- **Copy-per-container is a trap**: N divergent copies + one refresh = the other N−1 copies (and the host original) can go stale mid-flight. **[cited mechanics, inferred consequence]**
- Interactive use of the *same* ChatGPT account elsewhere can invalidate the fleet's `auth.json` (argues for a dedicated account, §4).
- Mitigation: **one canonical `auth.json` per host machine**, bind-mounted (rw) into every container on that host — one file ⇒ no divergent copies; refresh is rare (~8-day cadence) so the write-race window is tiny. This matches the official "one auth.json per runner" framing where runner = your host. **[inference, consistent with official doc]**

**Non-interactive execution**: `codex exec` is the supported headless run mode; `/status` shows remaining limits. There is also enterprise-only `codex login --with-access-token` + PATs (`at-…`) for CI — not available on Plus/Pro (cited: pi-codex-token docs, learn.chatgpt.com).

### 1.2 pi (pi.dev / earendil-works, Mario Zechner)

**Auth modes.** `/login` in the TUI offers **subscription OAuth for Claude Pro/Max, ChatGPT Plus/Pro (Codex), GitHub Copilot, xAI, OpenRouter**, or API keys via env (`ANTHROPIC_API_KEY` etc.) / `--api-key` flag. Resolution order: CLI flag → `auth.json` entry → env var → `models.json` custom keys ([providers.md](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/providers.md)).

**Storage.** Everything in **plain JSON `~/.pi/agent/auth.json`, `0600`** — "Pi does NOT use macOS Keychain, keytar, keyring, or any OS-level credential store" (cited: docs + Agent Safehouse sandbox analysis of pi). Perfect for mounting.

**Headless login.** Officially handled: on remote/headless machines where the browser can't reach the loopback callback, "paste the final redirect URL (or the authorization code) into the login prompt instead" (cited: providers.md). So you can even complete first-time OAuth *inside* a container with the cred dir mounted. Tokens "auto-refresh when expired" and are written back to `auth.json`.

**Headless run.** `pi --rpc` = JSON-over-stdio service; also plain one-shot prompts. **[cited]**

**The Anthropic problem.** pi's Claude Pro/Max login still *authenticates*, but since Anthropic's 2026 policy change third-party OAuth **no longer draws on plan limits**: users get "Third-party apps now draw from your extra usage, not your plan limits. Add more at claude.ai/settings/usage" — i.e., per-token billing on the consumer account (cited: earendil-works/pi issues [#3670](https://github.com/earendil-works/pi/issues/3670) "Anthropic subscription auth bills per token despite Claude Pro/Max", #3808, #3372). So pi+Claude-sub gives you *convenience* OAuth, not subscription economics.

**ChatGPT/Codex via pi**: uses the Codex OAuth backend; there is also `pi-codex-token` for enterprise PATs (explicitly "PATs are not auto-refreshable… mint a new PAT" on 401; Plus/Pro accounts can't use it). For Plus/Pro the normal `/login` OAuth (with auto-refresh into `auth.json`) is the path. **[cited]**

### 1.3 opencode (anomalyco / sst)

**Auth modes.** `opencode auth login` / `/connect`; OAuth for **OpenAI ChatGPT Plus/Pro** ("open your browser and ask you to authenticate"), GitHub Copilot (device code at github.com/login/device), GitLab Duo, etc.; API keys and env vars otherwise. Docs: "For CI or headless environments, set a PAT or JWT before starting opencode" for the providers that support that ([opencode.ai/docs/providers](https://opencode.ai/docs/providers/)).

**Storage.** `~/.local/share/opencode/auth.json` (XDG data dir; `opencode auth list` to inspect). File-based, no Keychain. OAuth token refresh is automatic. **[cited docs; "no keychain" inferred from path + working ro/rw mount patterns in the wild]**

**Container patterns in the wild:** mount the auth file, e.g. `-v "$HOME/.local/share/opencode/auth.json":/root/.local/share/opencode/auth.json` (agileweboperations.com how-to; danz.blog devcontainers; christiangoelz/opencode-docker; dev.to sayem314 mounts `~/.config/opencode` + `~/.local/share/opencode` rw). Some guides show `:ro` mounts — **that breaks refresh write-back**; rw is required for OAuth longevity **[cited pattern, inferred failure mode — consistent with "let OpenCode refresh auth.json during runs and keep the updated file for the next job" guidance in CI-focused writeups]**.

**Headless first-login:** community plugin `tumf/opencode-openai-device-auth` adds a device-code flow precisely for "SSH, Docker, remote servers." There's also `numman-ali/opencode-openai-codex-auth` (ChatGPT Plus/Pro via the Codex OAuth client; auto-refresh; manual-URL-paste fallback for SSH/WSL; README: "personal development use only", user is responsible for OpenAI ToS compliance). **[cited]**

**The Anthropic problem (worse than pi's).** Anthropic **server-side blocked** opencode/Cline/RooCode subscription OAuth on **Jan 9, 2026** (no warning, then escalating client fingerprinting), and on **Feb 19, 2026** added an explicit ToS clause: **OAuth tokens from Free/Pro/Max "may not be used with third-party tools or the Agent SDK"** — Claude-sub tokens are for Claude Code and Claude.ai only; a final cutoff for OpenClaw-style platforms landed April 4, 2026 (cited: alternativeto.net news, zbuild.io "OpenCode Blocked by Anthropic", GIGAZINE, dev.to/mcrolly, HN #46549823). The current opencode provider docs list ChatGPT/Copilot/GitLab-Duo subscriptions — **no Claude Pro/Max** — consistent with removal. Community plugins that pipe Claude-Code credentials into opencode (griffinmartin/opencode-claude-auth, cemalturkcan/…) exist but are exactly what the ToS clause bans and what fingerprinting hunts. **Do not build on them.**

### 1.4 Claude Code — sharp specifics only (other agent owns this)

- macOS stores the OAuth credential in **Keychain** ("Claude Code-credentials") — the *only* harness of the four with the Keychain problem; Linux/container fallback is `~/.claude/.credentials.json` (cited: code.claude.com/docs authentication, community gists).
- `claude setup-token` mints a **long-lived OAuth token** for CI/headless use via `CLAUDE_CODE_OAUTH_TOKEN` — the clean way to get Max-subscription auth into a Linux container without Keychain (cited: docs; known bugs: #19274 token printed but not saved; **#37512 setting `CLAUDE_CODE_OAUTH_TOKEN` can silently delete the macOS Keychain entry on exit** — keep the env var confined to containers, never set it in your host shell on the Mac).
- The "login once inside the container with a mounted `~/.claude`" pattern is well documented (foldr.uk "Use Your Claude Code Pro Subscription in Docker"; Anthropic's own devcontainer reference). First-party use of your own sub in a container is fine; it's *third-party* tools that are banned.

---

## 2. Getting the credential into the container — what works, what breaks

**Works (all three harnesses):**
1. **Login on the host once** (mac: normal browser flow; Linux host: device-auth / paste-URL / `ssh -L 1455:…`).
2. **Bind-mount the minimal credential path rw** into the container at the container user's home:
   - Codex: `-v ~/.codex:/root/.codex` (or a dedicated `CODEX_HOME`)
   - pi: `-v ~/.pi/agent:/root/.pi/agent`
   - opencode: `-v ~/.local/share/opencode:/root/.local/share/opencode` (+ `~/.config/opencode` for config)
3. Run headless (`codex exec`, `pi --rpc`/one-shot, `opencode run`).

This is exactly the pattern in dev.to/sayem314 ("Run Codex, Claude Code, and OpenCode in Docker — Without Giving Them Your Host": rw mounts of the config/auth dirs, login on host, ephemeral containers) and the official Codex docs/CI-CD page.

**Breaks / gotchas:**
- **macOS Keychain unreachable from Linux containers** — only bites Claude Code; Codex defaults to file (don't set `keyring`), pi and opencode are file-only. **[cited]**
- **`:ro` mounts break refresh write-back.** All three write refreshed tokens into their auth.json. Codex refreshes rarely (~8-day cadence or 401) so ro mounts *appear* to work for a week, then everything 401s at once. Mount rw. **[cited cadence; inferred failure shape]**
- **Copy-into-ephemeral-container loses the refresh.** With `docker run --rm` + `docker cp`, a refresh that happens inside the container is discarded; worse, because **OpenAI refresh tokens rotate**, the host's original copy may now hold a *consumed* refresh token → `refresh_token_reused` auth loop, recover with `rm ~/.codex/auth.json` + re-login (cited: codex #19803 and the official CI/CD page's write-back requirement). **Bind-mount the one canonical file instead of copying.**
- **Concurrency**: official Codex guidance — one `auth.json` per runner or serialized stream; never the same file on multiple *machines* concurrently. N containers on one host sharing one bind-mounted file is the closest safe interpretation; the residual race (two containers refreshing simultaneously in the same ~minute after the 8-day boundary) is accident-grade-acceptable, and you can eliminate it by "pre-warming" (run a trivial `codex exec` on the host before a generation so `last_refresh` is fresh). **[official rule cited; per-host reading and pre-warm are inference]**
- **Signing into the same account elsewhere** (your laptop TUI, VS Code extension, another machine) can rotate the token and brick the fleet's file mid-run (#19803). Dedicated account (§4) largely removes this.
- **uid/permission drift**: containers running as root leave root-owned files in the mounted dir on Linux hosts; match uids or chown after (cited: sayem314 gotchas).
- **Env-var injection of a raw access token** (the Docker cagent model: `CHATGPT_OAUTH_TOKEN` for headless) works but the token "expires and is not refreshed" — fine for minutes-long runs seeded fresh each time, wrong for a long-running fleet (cited: docs.docker.com/ai/docker-agent/providers/chatgpt — also a nice precedent: Docker's own agent does ChatGPT OAuth with a plain file at `~/.config/cagent/chatgpt-auth.json`, and warns "Sign-in is per user; do not share the stored credential").

---

## 3. Established patterns (repos / posts / issues)

- **Official**: Codex auth docs (`docker cp` recipe; device-auth; port-1455 SSH forward) and the **CI/CD account-auth page** (seed → run → write back; one file per runner; ~8-day refresh) — [learn.chatgpt.com/docs/auth](https://learn.chatgpt.com/docs/auth), [learn.chatgpt.com/docs/auth/ci-cd-auth](https://learn.chatgpt.com/docs/auth/ci-cd-auth).
- **Multi-tool Docker wrappers**: dev.to/sayem314 (codex+claude+opencode, rw mounts of auth dirs, host login); christiangoelz/opencode-docker; danz.blog opencode devcontainers; agileweboperations opencode-in-docker; foldr.uk (Claude Pro sub in Docker).
- **Headless-login shims**: tumf/opencode-openai-device-auth (device code for ChatGPT in Docker/SSH); numman-ali/opencode-openai-codex-auth (ChatGPT Plus/Pro OAuth for opencode, manual URL paste); pi's built-in paste-redirect-URL flow.
- **Cautionary tales**: openai/codex #19803 (refresh_token_reused loop), #2798/#3820 (headless OAuth request → device-auth shipped), cc-switch #4474 / 9router #1444, #1663 (routers losing rotated refresh tokens), openclaw #57399 (silent refresh failure → re-auth every 10–30 days), earendil-works/pi #3670/#3372 (Anthropic sub → per-token extra usage).

---

## 4. ToS and rate limits

**OpenAI / ChatGPT.**
- "Sign in with ChatGPT" is an **official third-party program** (piloted in Codex CLI 2025, broad launch 2026). Rules for integrators: protect each user's credentials, use them only for requests that user authorizes, **"do not pool, share, or redistribute access tokens," "do not bypass rate limits, restrictions, or safeguards"** (cited: help.openai.com "Sign in with ChatGPT"; medianama; techtimes). So opencode/pi using ChatGPT OAuth sits on comparatively firm ground — a stark contrast with Anthropic.
- Codex usage on Plus/Pro: rolling **5-hour window shared across local + cloud tasks + all your devices**, plus **weekly limits**; on **July 12, 2026** OpenAI *temporarily* removed the 5-hour limit for Plus/Pro/Business (weekly cap still applies); overflow is purchasable as **credits**; check `/status` in-CLI (cited: chatgpt.com/codex/pricing, eesel.ai writeup, openai/codex discussion #2251, inventivehq, ofox.ai). Parallel mutators burn the *shared* windows — nothing forbids parallel `codex exec` per se, and Codex cloud itself parallelizes, but N containers all draw one account's quota. **[cited limits; parallel-is-quota-bound is inference]**
- Account sharing between *people* is against ChatGPT terms; a **dedicated sandbox account** (its own paid Plus/Pro) used by your own automation is materially safer operationally (no cross-device refresh-token rotation from your interactive laptop sessions; limit exhaustion and any abuse-flagging land on the sandbox account, not your daily driver). Buying a second account for *more capacity* is arguably fine (you pay for it); farming many accounts to dodge limits would collide with "do not bypass rate limits." **[inference / judgment — terms don't address one-human-multiple-accounts squarely]**

**Anthropic.**
- **Feb 19, 2026 consumer-ToS update**: OAuth tokens from Free/Pro/Max are for **Claude Code and Claude.ai only**; use "with any other products, tools, or services, including the Agent SDK, is unauthorized" — preceded by Jan 9, 2026 server-side blocking and followed by fingerprinting and the April 4, 2026 OpenClaw cutoff (cited: alternativeto, GIGAZINE, zbuild, dev.to/mcrolly). Third-party OAuth that still connects (pi) bills **per-token "extra usage,"** not plan limits (cited: pi #3670).
- Claude Max **inside Claude Code inside a container is first-party and fine** (weekly + 5-hour session limits apply; that side is your other agent's report).
- Net: for subscription-priced Anthropic tokens, the only mutator is Claude Code itself; pi/opencode can only reach Anthropic at metered prices (API key or "extra usage").

---

## 5. "Keep credentials out of the sandbox" — is a host-side auth proxy feasible for *account* auth?

First, the honest framing: **with account auth, if the harness runs in the container, the credential is in the container** — the harness process must hold a bearer token to call the API. A proxy only changes *which secret* is in the sandbox if (i) the harness supports pointing at a proxy without its own creds, and (ii) something host-side injects auth.

- **Codex, API-key mode**: solved officially — `@openai/codex-responses-api-proxy` (in openai/codex) exists precisely for privilege separation: privileged process pipes the key via stdin (`printenv OPENAI_API_KEY | codex-responses-api-proxy`), mlocks it, forwards only `POST /v1/responses`; sandboxed codex points at it via `model_providers.*.base_url` + `codex -p proxy`. **But it is API-key only — no OAuth, no refresh** (cited: README). So the official proxy does not carry subscription auth.
- **Codex, ChatGPT mode**: no official injection hook. Community proxies that expose ChatGPT-Plus/Pro Codex quota as an OpenAI-compatible endpoint exist (wowyuarm/codex-proxy, icebear0828/codex-proxy, Securiteru/codex-openai-proxy, EvanZhouDev/openai-oauth, 9router, chatmock-class tools). They all re-implement the Codex OAuth client + Responses-API quirks; their own issue trackers document the fragility (rotation desync → "Token invalid or revoked", incomplete auto-refresh after several days). Feasible, reverse-engineered, moving-target. **[cited]**
- **Anthropic account auth via proxy**: this is exactly the category Anthropic ToS-banned and actively fingerprints. Non-starter. **[cited]**
- Also note config plumbing: pointing codex at a proxy via `model_providers` **drops it out of ChatGPT-auth mode** (custom providers use API-key-style auth), so you can't currently say "codex, use your ChatGPT login but through my base_url." The subscription entitlement check lives between the official client and OpenAI's backend. **[inference from config docs + proxy projects' existence]**

Conclusion for Q5: a host-side auth proxy for **API keys** is mature and even official (Codex). For **subscription** auth it requires impersonating each harness's OAuth client and API dialect per provider — fragile by construction, and for Anthropic prohibited. Not worth it at accident-grade.

---

## 6. Recommendation for mantaray

Goal: container-per-run, harness-agnostic mutators on mac+Linux, subscription auth, accident-grade.

**Recommended: (a) mount the host credential into the container — with one canonical copy per host, rw.**

Concretely:
1. Per harness, keep one **auth home on the host**, logged in interactively once (mac: normal browser; Linux box: `codex login --device-auth`, pi's paste-redirect-URL, opencode device-auth plugin).
2. Per run: `docker run --rm` with **rw bind mounts of only the credential dirs**, mapped to the container user's home:
   - `~/.codex → /home/agent/.codex` (or set `CODEX_HOME`)
   - `~/.pi/agent → /home/agent/.pi/agent`
   - `~/.config/opencode` + `~/.local/share/opencode → /home/agent/...`
   - (Claude Code: `~/.claude` dir with `.credentials.json`, or `claude setup-token` → `CLAUDE_CODE_OAUTH_TOKEN` env injected per-run; never set that env var in your Mac host shell — issue #37512.)
3. **Never copy auth files per container** (rotation → `refresh_token_reused`); the bind mount *is* the write-back channel the official Codex CI doc requires, and it keeps exactly one token lineage per host.
4. Serialize or pre-warm around the rare refresh: Codex refreshes at ~8-day staleness or on 401, so either accept the tiny race (accident-grade: fine) or refresh on the host before each generation.
5. **Use a dedicated subscription account per provider for the fleet** (a second Plus/Pro; Max likewise for the Claude-Code mutator). This decouples the fleet from your interactive sessions (the #1 cause of rotation collisions), contains rate-limit exhaustion, and contains any abuse-flag blast radius. One account per human+purpose, not many accounts to multiply quota.
6. Accident-grade hygiene: don't bake creds into images; `0600`; no `~/.ssh`/`~/.gitconfig` mounts unless needed; network egress open (OAuth refresh needs `auth.openai.com` / provider endpoints).

Accepted trade-off: the mutator can read its own token. That is inherent to account auth with an in-container harness (§5); at accident-grade (protecting against `rm -rf` and runaway scripts, not credential-exfiltrating adversaries) it's the right trade.

**Not recommended: (b) harness on host, code-execution redirected into the container.** Credential exposure is lowest, but it demands per-harness surgery — swap pi's `bash` tool for a `docker exec` custom tool, write an opencode plugin to intercept its shell tool, and Codex has no clean remote-executor hook at all (its sandbox is Seatbelt/Landlock around local exec). That destroys harness-agnosticism and couples you to each tool's plugin API — fragile along the axis you care about most (adding harnesses cheaply). Keep it in the back pocket as a hardening step for whichever harness you end up running 90% of the time. **[inference]**

**Rejected: (c) host-side auth proxy.** Official only for API keys (codex responses-api-proxy); for subscription auth it means maintaining reverse-engineered OAuth clients per provider (chatmock/codex-proxy-class fragility, documented rotation bugs), and for Anthropic it's ToS-banned and fingerprinted. Highest fragility, lowest legitimacy. **[cited + judgment]**

Strategic note: if the loop's economics assume flat-rate subscription tokens, the viable pairs are **Codex CLI ↔ ChatGPT Plus/Pro** and **Claude Code ↔ Max** (both first-party, both container-workable). pi and opencode remain excellent harness-agnostic *shapes* — but on Anthropic they now run at metered prices, and on OpenAI they ride the semi-official "Sign in with ChatGPT" path (working today, one policy decision away from the Anthropic story). Design the harness adapter so the auth mount is per-harness config, and you're insulated either way. **[judgment]**

---

## Sources

Official docs
- Codex auth: https://learn.chatgpt.com/docs/auth (redirect from https://developers.openai.com/codex/auth)
- Codex account auth in CI/CD: https://learn.chatgpt.com/docs/auth/ci-cd-auth
- Codex pricing/limits: https://chatgpt.com/codex/pricing/ ; https://developers.openai.com/codex/pricing
- codex responses-api-proxy README: https://github.com/openai/codex/blob/main/codex-rs/responses-api-proxy/README.md
- pi providers/auth: https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/providers.md ; https://pi.dev/packages/pi-codex-token
- opencode providers/auth: https://opencode.ai/docs/providers/
- Docker agent ChatGPT provider (precedent): https://docs.docker.com/ai/docker-agent/providers/chatgpt/
- Claude Code auth: https://code.claude.com/docs/en/authentication
- Sign in with ChatGPT: https://help.openai.com/en/articles/20001410-sign-in-with-chatgpt

Issues / discussions
- openai/codex #2798 (headless OAuth), #3820 (dupe), #19803 (refresh_token_reused loop), discussion #2251 (usage limits), discussion #8338 (forks + ChatGPT sign-in ToS)
- earendil-works/pi #3670, #3808, #3372, discussions #2950, #1510 (Anthropic sub → extra usage)
- anthropics/claude-code #19274, #37512, #80091 (setup-token / keychain / env-var precedence bugs)
- cc-switch #4474; 9router #1444, #1663; openclaw #57399; multica #2081 (rotation fragility in proxies/routers)

News (Anthropic third-party ban)
- https://alternativeto.net/news/2026/2/anthropic-officially-bans-using-subscription-authentication-for-third-party-claude-use
- https://gigazine.net/gsc_news/en/20260220-anthropic-third-party-block/
- https://www.zbuild.io/resources/news/opencode-blocked-anthropic-2026
- https://dev.to/mcrolly/anthropic-kills-claude-subscription-access-for-third-party-tools-like-openclaw-what-it-means-for-3ipc
- https://news.ycombinator.com/item?id=46549823

Patterns / posts
- https://dev.to/sayem314/run-codex-claude-code-and-opencode-in-docker-without-giving-them-your-host-1i3e
- https://foldr.uk/claude-code-pro-subscription-docker/
- https://danz.blog/blog/opencode-in-devcontainers
- https://agileweboperations.com/2025/11/23/how-to-run-opencode-ai-in-a-docker-container/
- https://github.com/christiangoelz/opencode-docker
- https://github.com/tumf/opencode-openai-device-auth ; https://numman-ali.github.io/opencode-openai-codex-auth/
- https://github.com/wowyuarm/codex-proxy ; https://github.com/Securiteru/codex-openai-proxy ; https://github.com/EvanZhouDev/openai-oauth
- https://www.eesel.ai/blog/gpt-remove-5-hour-limits ; https://inventivehq.com/blog/codex-subscription-options-guide ; https://ofox.ai/blog/codex-weekly-limit-drained-2026/
- Agent Safehouse pi sandbox analysis: https://agent-safehouse.dev/docs/agent-investigations/pi
