# GitHub OAuth Apps vs GitHub Apps for NetHackers hub registration

- **Date:** 2026-08-09
- **Status:** Research complete. Every mechanic below was verified against live docs.github.com pages fetched on 2026-08-09 (full source list at the end). Anything not confirmed by the docs is explicitly flagged in "What I could not confirm."
- **Question:** When `nethackers register` authenticates a contributor and the hub verifies their `github.com/<login>/nethacker` repo, should the platform credential be a **GitHub OAuth App** or a **GitHub App**?

---

## Executive summary

**Register a GitHub App. Do not build on an OAuth App.**

Both app types support the CLI-friendly device flow with an identical user experience (type an 8-character code at github.com/login/device), and both prove identity the same way (`GET /user`). The decision is made by requirements 3 and 4:

- **OAuth Apps have no read-only repo scope.** Reading a *private* repo requires the `repo` scope, which grants **full read/write on every repo the user can touch** (plus org projects, teams, webhooks). The moment the dev-phase private `nethacker` repo or a future private `nethacker-dev` repo enters the picture, an OAuth App forces you to hold exactly the kind of broad personal credential requirement 4 forbids. OAuth tokens also **never expire** unless revoked.
- **A GitHub App's tokens can never exceed the app's registered permissions** (`contents: read` + mandatory `metadata: read` for NetHackers). A leaked NetHackers GitHub App token can, at absolute worst, *read* code in repos its owner opted in — never write, never touch org resources. User tokens expire in 8 hours; installation tokens in 1 hour.
- **GitHub Apps scale for M2b.** Installation access tokens give the hub server-to-server read access to each contributor's repo with **no user token stored anywhere**, a **per-installation 5,000 req/hr budget** (vs one shared per-user budget), and webhooks that announce access changes.
- GitHub's own docs state the default: *"In general, GitHub Apps are preferred over OAuth apps"* — and GitHub's official tutorial for exactly our shape ("Building a CLI with a GitHub App") uses a GitHub App with device flow.

**Leanest safe start (M1):** GitHub App, `contents: read`, device flow enabled, webhook deactivated, no installation required. The CLI needs only the public client ID; the hub needs no GitHub secret at all. Identity + public-repo read both come from the short-lived user access token. Add the "install the app" step (and the app private key on the hub) only when private repos or M2b workers arrive. No OAuth App + GitHub App hybrid is needed — the GitHub App natively does the "Login with GitHub" half.

---

## Requirements → verdict at a glance

| # | Requirement | OAuth App | GitHub App |
|---|---|---|---|
| 1 | CLI identity via device flow | Yes | Yes (identical flow) |
| 2 | Prove control of `<login>/nethacker` | Login match only | Login match **and/or** installation proof |
| 3 | Read repo at commit; public now, maybe private in dev; good rate limits | Public: yes (5,000/hr). **Private: only via all-powerful `repo` scope** | Public: yes (5,000/hr). Private: `contents: read` on selected repos |
| 4 | Never hold a broad/personal credential | **Fails** the moment private repos appear | Holds only app-scoped, read-only, expiring tokens |
| 5 | M2b workers, many repos, `nethacker-dev` | Store long-lived user tokens with `repo` scope (bad) | Installation tokens, per-installation rate budgets, no user tokens stored |

---

## Background: the credential zoo

GitHub token prefixes, from [About authentication to GitHub]:

| Prefix | What it is | Issued to | Lifetime |
|---|---|---|---|
| `gho_` | OAuth App access token | OAuth Apps | **Does not expire**; revoked manually, on 1 year of disuse, or when leaked publicly |
| `ghu_` | GitHub App **user access token** ("user-to-server") | GitHub Apps | **8 hours** by default (expiry is opt-out) |
| `ghr_` | GitHub App **refresh token** | GitHub Apps | **6 months** (15,897,600 s) |
| `ghs_` | GitHub App **installation access token** ("server-to-server") | GitHub Apps | **1 hour** |
| `ghp_` / `github_pat_` | Classic / fine-grained personal access token | Users directly | User-chosen (fine-grained default 30 days) |
| — | App JWT (RS256, signed with app private key) | GitHub Apps | **≤ 10 minutes**; only calls app-management endpoints |

An OAuth App has exactly one credential story: client ID + client secret → long-lived user-scoped `gho_` tokens. A GitHub App has three authentication modes ([About authentication with a GitHub App]): as the **app** (JWT), as an **installation** (`ghs_`), and **on behalf of a user** (`ghu_`).

---

## Option A: OAuth App

### The workflow in plain English (applied to NetHackers)

*Setup once:* register an OAuth App under the NetHackers org; tick **Enable Device Flow**. Bake the client ID into the CLI (client IDs are public). The client secret exists but is **not needed** for anything in this flow — the docs are explicit: *"The `client_secret` is not needed for the device flow."*

1. **User** runs `nethackers register`.
2. **CLI** calls `POST https://github.com/login/device/code` with `client_id` and an **empty `scope`** → gets a `user_code` like `WDJB-MJHT` (valid 15 minutes) and `device_code`.
3. **CLI** prints: "Open https://github.com/login/device and enter WDJB-MJHT". **User** does so in any browser, on any machine, and clicks Authorize.
4. **CLI** polls `POST https://github.com/login/oauth/access_token` (every ≥5 s) → receives `gho_…`, a token that **never expires** and — with empty scope — grants *"read-only access to public information (including user profile info, repository info, and gists)"*.
5. **CLI** sends the `gho_` token to the **hub** with the registration payload (over TLS). This is *not* the user's `gh` token — it is a token minted for our app with zero scopes.
6. **Hub** calls `GET /user` with it → gets `login`; checks `login` matches the owner of the claimed `<login>/nethacker`; fetches the subtree at the pinned commit via the Git Trees/contents API (using the token → contributor's 5,000 req/hr budget, or unauthenticated → 60 req/hr/IP); recomputes the digest; stores only `login` + verdict; **discards the token**. If it doesn't discard it, it now holds a *non-expiring* credential.
7. **Nobody** can read a private dev repo at this point. To do that, re-run the flow requesting `scope=repo` — and now the token the hub touches can **read and write every repo, public and private, that the contributor can access**, indefinitely, until someone revokes it.

Step 7 is the entire problem with Option A.

### Tokens, lifetimes, scopes

- One token type: `gho_`, **no expiry** ([Differences]: *"OAuth tokens remain active until they're revoked by the customer"*). Safety nets: auto-revoked after **1 year unused**, auto-revoked if pushed to a public repo/gist, revocable by user/app owner/leak-reporting API ([Token expiration and revocation]).
- **Scopes are coarse and cannot be read-only for code** ([Scopes for OAuth apps]):
  - `(no scope)` — read-only public info. Genuinely safe; sufficient for identity + public-repo reads.
  - `public_repo` — read/**write** public repos. Already more than we want.
  - `repo` — *"full access to public and private repositories including read and write access to code …also grants access to manage organization-owned resources including projects, invitations, team memberships and webhooks."* The only way to read a private repo.
  - There is no `repo:read`. This is the structural dead end.
- Users can **edit scopes down** during authorization, so the hub must verify granted scopes (via the `X-OAuth-Scopes` response header) rather than trust what it requested.
- Max **10 live tokens per user/app/scope combo**; the 11th silently revokes the oldest ([Authorizing OAuth apps]) — a foot-gun for users registering from several machines.

### Device flow from the CLI

Exactly as described in the workflow; enablement is a checkbox on the app settings page. Rate limits on the flow itself: 50 user-code submissions/hour/app; `slow_down` errors add 5 s to the polling interval. Loopback-redirect web flow is also available for CLIs but device flow is the documented headless path.

### Proving repo ownership

Only one mechanism: token → `GET /user` → `login`, and check the claimed repo lives at `<login>/nethacker`. Sound for user-owned repos (only the account holder can authorize with that login), but there is no repo-granular proof, and nothing that would extend to org-owned repos later.

### Reading the repo

- **Public:** works with the no-scope token (or unauthenticated at 60/hr). Trees API (`GET /repos/{o}/{r}/git/trees/{sha}?recursive=1`) + blobs, contents API at a `ref`, or the tarball endpoint — all fine for digest checks.
- **Private (dev phase):** `repo` scope only. Full stop.

### Rate limits

- All `gho_` requests bill the **contributor's personal 5,000 req/hr budget**, shared with their PATs and every other app acting for them ([Rate limits]). Fine for registration bursts; unfriendly for M2b re-verification sweeps.
- Nice extra: an OAuth App may fetch **public** data with client-ID/secret Basic auth at **5,000 req/hr per app** — a documented server-side lane for public reads that doesn't touch any user budget.

### Security / blast radius

- **Hub compromised while holding no-scope tokens:** attacker can read public data as those users. Low harm — but the tokens never expire, so "briefly held" must be enforced by our code, not by GitHub.
- **Hub compromised while holding `repo`-scoped tokens (private-repo support):** attacker gets durable read/write over every repo of every registered contributor, plus org-resource management where they have rights. This is precisely requirement 4's nightmare; org OAuth-app access policies at contributors' employers may also block or expose these tokens.
- Client secret leak: low impact in a pure device-flow design (secret unused), but it guards nothing either.

### Revocability + auditability

- User revokes at `github.com/settings/connections/applications/<client_id>` (kills all that app's tokens); app owner can revoke via `DELETE /applications/{client_id}/token`; GitHub's unauthenticated credential-revocation API kills reported leaks. **No webhook tells the app it lost authorization** — you find out via 401s.
- API actions appear as the **user** in logs; no app-branded actor, no installation surface for a user to inspect per-repo grants (grants are account-wide per scope).

### Setup + infra cost

Cheapest possible: register app, enable device flow, ship client ID. No private key, no JWT, no installations, no webhooks (per-repo webhooks exist but are manual and are *not* auto-removed when access ends). One secret (client secret) that device flow never uses.

### Future fit (M2b, private `nethacker-dev`)

To read many repos in background workers you must **store** per-contributor `gho_` tokens — non-expiring, and `repo`-scoped once private repos matter — and spend the contributors' shared personal rate budgets. Repo-owned webhook management is manual. This is an architecture you would migrate off; GitHub even maintains a "Migrating OAuth apps to GitHub Apps" guide.

**Verdict:** Excellent for requirement 1, adequate for 2, passes 3 only while everything is public, structurally fails 4 the moment private repos appear, and fails 5.

---

## Option B: GitHub App

### The workflow in plain English (applied to NetHackers)

*Setup once:* register a GitHub App under the NetHackers org. Permissions: **Repository → Contents: Read-only** (Metadata: Read-only is added automatically). Tick **Enable Device Flow**. Leave **Expire user authorization tokens** on. Deselect **Active** under Webhook (docs: *"if your app will only be used for authentication… deselect this option"*). Bake the public client ID into the CLI. The client secret and the app private key exist but stay untouched until later phases.

**M1 — register a solution (public repo):**

1. **User** runs `nethackers register`.
2. **CLI** calls `POST https://github.com/login/device/code` with `client_id` → `user_code` (15 min TTL) + `device_code`. *(No scopes exist here; the app's registered permissions are the ceiling.)*
3. **CLI** prints "enter WDJB-MJHT at https://github.com/login/device"; **user** authorizes in a browser. Authorization does **not** require installing the app ([On behalf of a user]: *"An app does not need to be installed in order for a user to authorize the app"*).
4. **CLI** polls `POST https://github.com/login/oauth/access_token` → `ghu_…` (**expires in 8 h**) + `ghr_…` refresh token (**6 months**). CLI may keep the refresh token in the OS keychain to make later registrations silent — and can refresh **without any client secret**, a device-flow-specific allowance ([Refreshing user access tokens]: client secret *"Required unless the user access token was generated using the device flow"*).
5. **CLI** sends the `ghu_` token to the **hub** with the registration payload.
6. **Hub** calls `GET /user` → `login`; checks it matches the owner of `<login>/nethacker`; reads the subtree at the pinned commit **with the same token** — user access tokens carry an *implicit permission to read public resources* via REST and GraphQL ([Choosing permissions]) at the contributor's 5,000 req/hr — verifies the digest, stores `login` + verdict, discards the token. Even if it forgot to discard: the token dies by itself in ≤ 8 hours, and its maximum capability is *read*.

**M2 — private repos and background workers (adds one user-visible step):**

7. **CLI/hub** asks the contributor (once) to install the app: `https://github.com/apps/<app-slug>/installations/new`, choosing **Only select repositories → nethacker** (and later `nethacker-dev`). Only the account owner (or a repo admin) can do this — the installation itself is proof of control.
8. **Hub**, when it needs to read: signs a **JWT** with the app **private key** (RS256, ≤10 min), calls `GET /repos/{owner}/nethacker/installation` to resolve the installation ID, then `POST /app/installations/{id}/access_tokens` → `ghs_…` valid **1 hour**, optionally down-scoped further via `repositories:` / `permissions:` in the request body. It reads the subtree (API or even `git clone https://x-access-token:ghs_…@github.com/owner/repo.git`), and lets the token expire. **No user credential is ever stored; nothing long-lived exists outside the hub's own private key.**
9. **M2b workers** repeat step 8 per contributor. Each installation has its **own** 5,000 req/hr budget, so a re-verification sweep never starves (or is starved by) anyone.

### Tokens, lifetimes, permissions

- **User access token `ghu_`:** 8 h default; refresh `ghr_` 6 months; expiry is default-on and opt-out is discouraged ([Registering], [Refreshing]). No scopes — the `scope` response field is *always empty*; capability = **intersection** of (app permissions) ∩ (user's own access) ∩ (accounts where the app is installed), **plus** implicit public read ([Generating a user access token], [On behalf of a user], [Choosing permissions]). Optional hardening: pass `repository_id` at token exchange to pin the token to the single `nethacker` repo.
- **Installation access token `ghs_`:** 1 h; capability = app permissions ∩ installation's repo selection, down-scopable per token; *"the success of an API request with an installation access token only depends on the app's permissions"* ([Choosing permissions]).
- **App JWT:** RS256, `exp` ≤ 10 min, `iss` = client ID; only mints installation tokens and calls app-management endpoints ([Generating a JWT]).
- Permission changes to the app later (say, adding a permission) require each installer's explicit re-approval — upgrades can't silently widen access ([Choosing permissions]).

### Device flow from the CLI

Byte-for-byte the same UX and endpoints as the OAuth App version (same `login/device/code`, same 15-minute 8-char code, same 5 s polling, same enable-checkbox in settings); the only differences are the response now contains `expires_in`/`refresh_token`, and there is no `scope` parameter to send. GitHub's tutorial for this exact architecture is [Building a CLI with a GitHub App], which also tells CLIs to store tokens per-platform best practice (macOS keychain etc.). One flow-specific error worth handling: `unverified_user_email`.

### Proving repo ownership

Two independent mechanisms:
1. **Identity match** (same as OAuth): `GET /user` with the `ghu_` token → `login` must own the repo.
2. **Installation proof** (GitHub-App-only): the app appears installed on `<owner>/nethacker` (`GET /repos/{owner}/{repo}/installation` with the app JWT) — only someone with admin control of that account/repo can cause that. Also survives into a future where solutions live in org-owned repos, where login-match alone breaks.

### Reading the repo

- **Public:** `ghu_` token (implicit public read, 5,000/hr) — no installation needed; or unauthenticated (60/hr/IP); or `ghs_` after installation.
- **Private (dev, `nethacker-dev`):** installation with `contents: read` on selected repos; read via `ghs_` (or via `ghu_` if the requesting user also has access — intersection rule). Never more than read.

### Rate limits ([Rate limits])

- `ghu_` requests: contributor's personal **5,000 req/hr** (shared budget; 15,000 if the app is owned by a GitHub Enterprise Cloud org and requests are on behalf of its members).
- `ghs_` requests: **5,000 req/hr per installation**; scales +50/hr per repo beyond 20 repos and +50/hr per org user beyond 20, capped at 12,500/hr; installations on GHEC orgs get 15,000/hr flat. For NetHackers (one installation per contributor account) the per-installation floor is the win: N contributors ≈ N × 5,000/hr aggregate for M2b.
- Secondary limits apply to everyone: ≤100 concurrent requests, ~900 REST points/min/endpoint — batch M2b politely regardless.

### Security / blast radius

- **`ghu_` leak / hub compromise:** attacker can *read* public data plus repos in installations the victim can access — for ≤ 8 hours. No writes ever (app has none to give).
- **`ghs_` leak:** read `nethacker`(-dev) of one contributor for ≤ 1 hour.
- **App private key leak (the worst case):** attacker can mint installation tokens for **all** installations — i.e., *read* every registered contributor's opted-in repos until the key is revoked. Private keys never expire and must be revoked manually; an app can hold up to 25 keys so you can rotate without downtime ([Managing private keys]). Still zero write capability anywhere. Compare with the OAuth worst case (durable read/**write** over contributors' entire accounts): categorically smaller.
- All token types are auto-revoked by secret scanning if pushed to a public repo, and killable via the credential-revocation API.

### Revocability + auditability

- Contributor can (a) uninstall/suspend the installation or shrink its repo list — installation tokens lose access immediately and the change is visible to the app; (b) revoke the user authorization — GitHub then fires the **`github_app_authorization` webhook** (apps cannot unsubscribe from it), so the hub gets *told*, instead of discovering 401s ([Generating a user access token]).
- Server-to-server actions are attributed to the **app's bot identity**; user-to-server requests appear in audit/security logs as the user **with `programmatic_access_type: "GitHub App user-to-server token"`** ([On behalf of a user]) — clean forensic separation between "hub did this" and "contributor did this".

### Setup + infra cost

More pieces than an OAuth App, phased:
- **M1:** registration + client ID. That's all — device flow needs no secret, refresh needs no secret, public reads need no installation. Hub stores zero GitHub secrets.
- **M2/M2b:** hold the **private key** (a PEM in the hub's secret store; ~20 lines of PyJWT code or `githubkit`/`gidgethub` to mint JWTs → `ghs_`), cache installation tokens ≤ 1 h, handle the install redirect. Webhook endpoint stays optional; activate it later if we want push-notification of installation changes instead of polling.
- Local dev with the stub is unaffected; for live-GitHub dev, register a second throwaway GitHub App ("nethackers-dev") since callback URLs/keys differ per environment — registrations are free and unlimited for our own use.

### Future fit

This *is* the target architecture for M2b: per-contributor installations, hourly tokens minted on demand, per-installation rate budgets, optional webhooks for repo-list changes, no stored user credentials, private `nethacker-dev` covered by the same `contents: read`. The docs' one carve-out favoring OAuth Apps (enterprise-object APIs) is irrelevant to NetHackers.

**Verdict:** Meets all five requirements; the only cost is the JWT/installation machinery, which is deferrable past M1.

---

## Sidebar: fine-grained PATs (comparison only — not a candidate)

A contributor *could* hand-create a fine-grained PAT (single resource owner, selected repos, `contents: read`, default 30-day expiry, always includes public-repo read) and paste it into the CLI. It is the right *shape* of credential, but: it's manual UX (settings-page spelunking vs typing an 8-char code), it proves possession of a token rather than interactively proving identity, expiry/rotation burden lands on the contributor, and the hub ends up storing user-owned credentials again. Useful mental model — "a GitHub App installation is a fine-grained PAT the platform can mint for itself, hourly" — but not the mechanism. ([Managing your personal access tokens])

---

## Comparison table

| Dimension | OAuth App | GitHub App |
|---|---|---|
| Device flow for CLI | Yes (enable in settings; client ID only) | Yes (identical; client ID only) |
| "Login with GitHub" identity | `GET /user` with `gho_` | `GET /user` with `ghu_` |
| Access model | **Scopes** (coarse, account-wide) | **Fine-grained permissions** × chosen repos |
| Read-only code access | **Impossible for private repos** (`repo` = full R/W everything) | `contents: read`, per-repo |
| Public repo read w/o extra grants | Yes — no-scope token | Yes — implicit public read on `ghu_` |
| User token lifetime | **Never expires** (revocation-only; 1-yr-disuse cleanup) | **8 h** + 6-month refresh (default-on) |
| Server-to-server access | None (only stored user tokens) | Installation tokens, **1 h**, minted from private-key JWT (≤10 min) |
| Ownership proof | login match only | login match + installation-on-repo proof |
| Rate limits | User's shared 5,000/hr; app client-creds lane for public data 5,000/hr/app | User's 5,000/hr for `ghu_`; **5,000/hr per installation** (scales to 12,500; 15,000 on GHEC) |
| Blast radius if hub popped | With `repo` scope: durable **R/W over contributors' entire accounts** | Read-only over opted-in repos; all tokens self-expire ≤ 8 h / 1 h; key rotatable |
| Revocation signal to app | None (discover via 401) | `github_app_authorization` webhook + installation events |
| Audit attribution | Always "the user" | App bot (server-to-server) vs user + `programmatic_access_type` marker |
| Org-policy friction | Subject to org OAuth-app restrictions | Not subject to OAuth policies; explicit install grant instead |
| Webhooks | Manual per-repo, not auto-cleaned | Central, optional, auto-disabled on uninstall |
| Setup cost | Client ID (+unused secret) | M1: client ID. M2: + private key, JWT minting, install UX |
| M2b many-repo workers | Store non-expiring broad user tokens | Per-installation hourly tokens, per-installation budgets |
| GitHub's own guidance | *"GitHub Apps are preferred"*; migration guide exists | Recommended default; CLI tutorial matches our use case |

---

## Recommendation

**Register one GitHub App ("NetHackers Hub") and phase it in:**

- **Phase M1 (now):** Permissions `Contents: read` (+ implied `Metadata: read`); device flow enabled; user-token expiration left on; webhook inactive. CLI ships the client ID, runs device flow, sends the 8-hour `ghu_` token to the hub; hub verifies `GET /user` login == repo owner, reads the public subtree with the same token (contributor's 5,000/hr), verifies the digest, discards the token. CLI keeps only the refresh token, in the OS keychain. **The hub stores no GitHub secrets at all in this phase.**
- **Phase M2 (private dev repos / stronger proof):** add "Install the app on your `nethacker` repo" to onboarding (`https://github.com/apps/<slug>/installations/new`). Hub gains the app private key; reads via 1-hour installation tokens; installation doubles as cryptographic-grade proof of repo control.
- **Phase M2b (verification workers):** workers resolve `GET /repos/{owner}/{repo}/installation` → mint `ghs_` per contributor → read many repos in parallel on per-installation budgets. Optionally activate the webhook to track installs/uninstalls/repo-list changes instead of polling.

**Why not the hybrid (OAuth App for identity + GitHub App for repos)?** It buys nothing — a GitHub App's device flow *is* the "Login with GitHub" flow with the same UX — and costs a second registration, a second credential set, and two authorization prompts for users. The only hybrid worth keeping in mind is *internal* to the GitHub App: **user token for identity + installation token for repo reads**, which is exactly the phased design above.

**Leanest safe option overall:** M1 as specified (GitHub App, device flow only, no installations, no webhook, no hub-side secrets). It is the same implementation effort as the OAuth version — the endpoints are literally the same URLs — while every token involved is read-only and self-expiring, and it upgrades to M2b without migration.

---

## Gotchas and implementation notes

1. **Enable Device Flow is an explicit checkbox** for both app types; forgetting it yields `device_flow_disabled`.
2. **Poll discipline:** respect `interval` (5 s); `slow_down` adds 5 s. Device codes die after 15 min — restart the flow, don't retry the code.
3. **Refresh without secret works only for device-flow-born tokens.** If a web flow is ever added (hub UI login), that exchange and its refreshes need the client secret server-side.
4. **Rotation invalidates:** using a refresh token kills the old access+refresh pair — the CLI must persist the new pair atomically.
5. **`repository_id` down-scoping** at token exchange can pin the `ghu_` token to the single `nethacker` repo (numeric ID; fetchable unauthenticated for public repos). Optional hardening.
6. **Handle `unverified_user_email`** in device flow (user must verify their primary email first).
7. **Don't cache installation IDs forever** — uninstall/reinstall changes them; re-resolve via `GET /repos/{owner}/{repo}/installation` on failure.
8. **Rate-limit etiquette for M2b:** ≤100 concurrent requests globally, ~900 REST points/min/endpoint (secondary limits), regardless of per-installation budgets.
9. **User tokens bill the contributor's shared 5,000/hr budget** — keep registration-time reads to a handful of requests (tree + needed blobs, or one tarball).
10. **Permission bumps require re-approval** by every installer — start with `contents: read` and think hard before ever requesting more.
11. **Tokens pushed to public repos are auto-revoked** by GitHub secret scanning — good backstop, don't rely on it.
12. **Per-environment apps:** register a separate dev GitHub App for live-GitHub testing; keep the prod private key out of dev configs. (Cluster-safety note: no interaction — this work is all web-service side.)

## What I could not confirm on docs.github.com (flagged, not guessed)

- **Whether an installation token (`ghs_`) can read *public* repos outside its own installation.** The docs define installation access in terms of the installation's own resources and are silent about foreign public repos. In practice GitHub Actions' installation-type `GITHUB_TOKEN` fetches public actions, suggesting yes — but verify in a 10-minute spike before M2b assumes it; the safe design (install on every repo the workers read) doesn't need it.
- **Whether the `ghu_` implicit public-read extends to git-over-HTTP** (`git clone` of a *public* repo with a user token, app not installed). Docs grant implicit public read for "REST API and GraphQL" only; Git access docs discuss tokens in installed contexts. Use the REST tarball/trees endpoints for M1 reads and this never matters.
- **Rate limits for raw/codeload/git-protocol fetches** are not documented; treat only the REST numbers above as contractual.
- **Whether the OAuth-style client-ID/secret Basic-auth lane for public data also works for GitHub App client credentials** — documented only under "Primary rate limit for OAuth apps." Not needed in the recommended design.

## Sources (docs.github.com, fetched 2026-08-09)

1. [Differences between GitHub Apps and OAuth apps](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/differences-between-github-apps-and-oauth-apps) — "GitHub Apps are preferred"; token identification table; 1-hour installation tokens; OAuth tokens active until revoked; webhooks/Git-access comparison.
2. [Authorizing OAuth apps](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps) — OAuth device flow mechanics; "client_secret is not needed for the device flow"; 15-min codes; 10-token cap; error codes.
3. [Scopes for OAuth apps](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps) — no-scope = public read-only; `repo`/`public_repo` definitions; users may downgrade scopes; `X-OAuth-Scopes`.
4. [Generating a user access token for a GitHub App](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-user-access-token-for-a-github-app) — GitHub App device flow; `ghu_`/`ghr_` lifetimes (28,800 s / 15,897,600 s); intersection rule; `repository_id`; `github_app_authorization` webhook; `unverified_user_email`.
5. [Refreshing user access tokens](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/refreshing-user-access-tokens) — 8 h/6 mo; expiry default-on; client secret required *unless device flow*; rotation invalidation.
6. [Authenticating as a GitHub App installation](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/authenticating-as-a-github-app-installation) — installation tokens, 1 h expiry, down-scoping params, git clone via `x-access-token`, installation-ID lookup endpoints.
7. [Generating an installation access token](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-an-installation-access-token-for-a-github-app) — `POST /app/installations/{id}/access_tokens`; ≤500 repos per token.
8. [Generating a JWT for a GitHub App](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-json-web-token-jwt-for-a-github-app) — RS256; `exp` ≤ 10 min; `iss` = client ID.
9. [Authenticating with a GitHub App on behalf of a user](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/authenticating-with-a-github-app-on-behalf-of-a-user) — authorization without installation; three access constraints; audit-log `programmatic_access_type`.
10. [About authentication with a GitHub App](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/about-authentication-with-a-github-app) — the three auth modes.
11. [Choosing permissions for a GitHub App](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/choosing-permissions-for-a-github-app) — **implicit public-read for user tokens**; installation-token success depends only on app permissions; permission-change re-approval; Contents permission for Git access.
12. [Registering a GitHub App](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/registering-a-github-app) — webhook "Active" optional; user-token expiration default-on.
13. [Building a CLI with a GitHub App](https://docs.github.com/en/apps/creating-github-apps/writing-code-for-a-github-app/building-a-cli-with-a-github-app) — GitHub's reference implementation of our exact pattern; secure token storage advice.
14. [Rate limits for the REST API](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api) — 60/hr unauthenticated; 5,000/hr per user (shared); per-installation 5,000→12,500 scaling, 15,000 GHEC; OAuth client-creds public lane; secondary limits.
15. [Token expiration and revocation](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/token-expiration-and-revocation) — 1-year-disuse revocation; push-to-public auto-revocation; revocation API + prefix list; user-token 8 h default.
16. [Managing your personal access tokens](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens) — fine-grained PAT properties (30-day default expiry, public-repo read always included, single resource owner).
17. [About authentication to GitHub](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/about-authentication-to-github) — token prefix table incl. `ghs_`.
18. [Managing private keys for GitHub Apps](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/managing-private-keys-for-github-apps) — up to 25 keys per app; keys never expire, manual revocation; rotation guidance; key fingerprints.
