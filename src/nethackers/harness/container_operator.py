"""Build the ``docker run …`` argv that wraps a host coding-agent CLI inside a
resource-capped, no-new-privileges container -- the mutator's execution cage.

Cgroup caps (``--pids-limit``/``--memory``/``--cpus``) are the fork-bomb /
runaway-process defense the host operator (``harness/operator.py``) lacks;
``--memory-swap`` is pinned to the same value as ``--memory`` so a runaway
can't just push into swap and reach ~2x the intended cap (Docker's default
otherwise allows exactly that). An in-container wall-clock ``timeout``
backstops a wedged harness. The in-cage harness command reuses the existing
host argv builders so all three harnesses stay a single source of truth, with
each harness's own confirmation-prompt
escape hatch swapped for the equivalent that's safe to use *because* the
container is already the sandbox: codex's
``--approve-for-me`` (host-side "don't ask me") is replaced with
``--dangerously-bypass-approvals-and-sandbox`` (codex's own inner sandbox
would otherwise double-sandbox and fail inside the container); claude gets
``--dangerously-skip-permissions`` appended outright, since claude has no
"already sandboxed" flag of its own.

``ContainerOperator``'s ``broker`` flag (default ``False``) is a SELECTABLE
alternative to the credential mount above: when set, ``run`` starts one
``cred_broker.CredBroker`` per credential that needs brokering, for the
container's whole lifetime, in place of ``auth_docker_args``. claude/codex
each have exactly one upstream/credential, so that's a single broker via
``auth_inject.auth_broker_args`` -- claude by a base-URL env + placeholder key
(no ``-v`` mount), codex by a ``-c model_providers.…`` INVOCATION override
(``operator._codex_cmd``, threaded through ``build_docker_argv``'s
``broker_base``) pointing codex at a CUSTOM UNAUTHENTICATED provider. That
override survives ``codex exec --ignore-user-config`` (which discards
``~/.codex/config.toml`` -- why the earlier cage ``openai_base_url`` config
was silently ignored), and, carrying no ``requires_openai_auth``/``env_key``,
makes codex send its POST with NO credential -- the broker injects 100% of
the auth host-side. codex's cage ``~/.codex`` is therefore just an EMPTY,
writable dir + ``CODEX_HOME`` (it needs a writable ``$CODEX_HOME`` for its
app-server socket/state); no token or config crosses the boundary. OpenCode 2
is multi-provider -- its base-URL override
is a per-provider JSON field, not one env var -- so it starts one broker PER
BROKERABLE PROVIDER instead, via ``auth_inject.opencode2_broker_targets``
(resolve) / ``opencode2_broker_docker_args`` (rewrite the cage config);
``auth_broker_args`` itself stays claude/codex-only and is never called for
opencode2. A config with no brokerable provider falls back to the credential
mount wholesale. The mount stays the default because live per-agent
base-URL-override behavior is unverified -- see ``auth_inject``'s module
docstring for the broker path's known concerns.
"""
from __future__ import annotations

import contextlib
import os
import platform
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit

from nethackers.containers import label_args
from nethackers.harness.auth_inject import (
    auth_broker_args,
    auth_docker_args,
    broker_credential as _default_broker_credential,
    opencode2_broker_docker_args,
    opencode2_broker_targets,
)
from nethackers.harness.cred_broker import CredBroker, HeaderRewrite
from nethackers.harness.operator import (
    OperatorResult,
    _claude_cmd,
    _codex_cmd,
    _opencode2_cmd,
    run_operator,
)
from nethackers.harness.sandbox_preflight import mutator_platform_args

# The provider API host CredBroker forwards to, per harness -- fixed at
# construction (CredBroker is a single-upstream proxy, never open-relay). The
# broker forwards `upstream_base + request.path`.
# Codex's backend is the ChatGPT-SUBSCRIPTION one (`auth_mode: chatgpt`): a
# subscription login talks to `chatgpt.com/backend-api/codex/responses`, NOT
# the plain-API-key `api.openai.com`. This endpoint is VERIFIED via research
# (codex-rs source + OpenCode's plugin + several third-party proxies), not a
# guess. The upstream is the HOST ONLY (`https://chatgpt.com`): codex's `-c`
# provider `base_url` already ends in `/backend-api/codex`, so codex POSTs
# `/backend-api/codex/responses` and `upstream + path` reconstructs the full
# `https://chatgpt.com/backend-api/codex/responses` -- doubling the prefix
# here would 404. (See _codex_cmd for the `-c` routing and auth_inject's
# "Codex broker" section for the injected Authorization/ChatGPT-Account-Id.)
_BROKER_UPSTREAM_BASE = {
    "claude": "https://api.anthropic.com",
    "codex": "https://chatgpt.com",
}


def _broker_upstream_base(harness: str) -> str:
    try:
        return _BROKER_UPSTREAM_BASE[harness]
    except KeyError:
        raise ValueError(f"no broker upstream configured for harness: {harness!r}") from None


class _CredBrokerLike(Protocol):
    """The two calls ``ContainerOperator.run`` makes on whatever
    ``cred_broker_factory`` returns -- real ``cred_broker.CredBroker`` or a
    test's fake, never depended on for anything else."""

    def start(self) -> str: ...
    def stop(self) -> None: ...


def _host_gateway_url(base_url: str) -> str:
    """``http://<broker-bind-host>:<port>`` (what ``CredBroker.start()``
    returns) -- reachable from the host, but that host/loopback *inside* the
    container is the container itself, not the host. Re-hosts the same port
    onto ``host.docker.internal``, which ``build_docker_argv``'s ``--add-host
    host.docker.internal:host-gateway`` (added on the broker path) makes
    resolvable from inside the container."""
    parts = urlsplit(base_url)
    return urlunsplit(parts._replace(netloc=f"host.docker.internal:{parts.port}"))


def _bridge_gateway_ip(runtime: str, *, run=subprocess.run) -> str | None:
    """The default-bridge GATEWAY IP the sandbox's ``host.docker.internal``
    resolves to on native Linux (docker0 is 172.17.0.1 by default). The broker
    binds HERE on Linux so the container can reach it -- reachable from the
    docker bridge, NOT the host's public interface (never 0.0.0.0). Docker
    Desktop (macOS) routes ``host.docker.internal`` to the host loopback
    instead, so there the broker keeps binding 127.0.0.1. Returns ``None`` if
    the gateway can't be read; the caller then falls back to loopback (which
    fails LOUD on Linux rather than silently exposing 0.0.0.0)."""
    try:
        result = run(
            [runtime, "network", "inspect", "bridge",
             "-f", "{{range .IPAM.Config}}{{.Gateway}}{{end}}"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    gateway = (getattr(result, "stdout", "") or "").strip()
    return gateway or None


@dataclass(frozen=True)
class ContainerCaps:
    pids: int = 512
    memory: str = "8g"
    cpus: str = "4"
    timeout_s: int = 28800  # 8h: a real mutation may experiment against live NLE
                            # for a long time; this is a runaway ceiling, not a
                            # target -- the agent exits (and the container is
                            # reaped) as soon as it finishes.


# Module-level singleton, not a `ContainerCaps()` call in ContainerOperator's
# own signature (ruff B008): safe to share because ContainerCaps is frozen.
_DEFAULT_CAPS = ContainerCaps()


def build_docker_argv(
    *,
    harness: str,
    image: str,
    name: str,
    worktree: Path,
    cli: str | None,
    model: str | None,
    effort: str | None,
    caps: ContainerCaps,
    auth_args: list[str],
    brief: str,
    refs: Path | None = None,
    docker: str = "docker",
    extra_args: list[str] | None = None,
    userns_args: list[str] | None = None,
    broker_base: str | None = None,
) -> list[str]:
    """Assemble ``docker run`` argv for one mutator iteration: fixed docker
    prefix (name + caps + security-opt + extra_args + workspace mount), then
    ``auth_args``, then ``image``, then an in-container wall-clock
    ``timeout``, then the in-cage harness command.

    ``brief`` flows straight into the inner ``_claude_cmd``/``_codex_cmd``
    call and is required (no placeholder default): a caller that forgets it
    would otherwise silently build a wrong-prompt argv instead of failing
    loudly. Callers that only care about argv shape (this module's own tests)
    pass a fixed ``brief="B"``; the operator that wraps this function (a
    later task) passes the real per-iteration brief.

    ``userns_args`` are extra uid-mapping ``run`` flags, supplied by
    ``containers.nonroot_userns_args`` (which probes the runtime to decide) and
    merely carried here, so this stays pure argv assembly. Empty/``None`` --
    docker and rootful podman -- keeps the argv byte-identical to before the
    option existed; under ROOTLESS podman it is what stops the entrypoint's
    drop to ``agent`` from landing on a ``/workspace`` it can't write (#54).

    ``refs``, when given, is bind-mounted read-only at ``/refs`` -- the
    reference folders ``refs.assemble`` (a separate task) builds for the
    coding agent to read from inside the sandbox. ``None`` (the default)
    keeps the argv byte-identical to before this mount existed, so callers
    that don't yet have a refs dir (and all pre-existing tests) are unaffected.

    ``extra_args``, when given, is spliced in right after the fixed cap/
    security-opt block -- today only ``ContainerOperator``'s broker path uses
    it, for ``--add-host host.docker.internal:host-gateway`` (the container
    needs a route to the host-side broker). ``None`` (the default) keeps the
    argv byte-identical to before this existed.

    ``broker_base``, when given, is the host-gateway base URL of the codex
    credential broker; it is threaded into the in-cage ``codex exec`` command
    as the ``-c model_providers.…`` provider override that routes codex's
    model endpoint at the broker (see ``operator._codex_cmd``). It is
    consumed by the codex branch ONLY -- claude routes via an ``ANTHROPIC_BASE_URL``
    env in ``auth_args`` and opencode2 via its per-provider cage config, so both
    pass ``None`` here. ``None`` (the default, and every non-broker run) keeps
    codex on its mounted-``~/.codex`` auth with a byte-identical argv.
    """
    argv = [
        docker, "run", *mutator_platform_args(image), "--rm",
        "--name", name, *label_args(),
        "--pids-limit", str(caps.pids),
        "--memory", caps.memory,
        "--memory-swap", caps.memory,  # cap swap too -- else a runaway reaches ~2x via swap
        "--cpus", caps.cpus,
        "--security-opt", "no-new-privileges",
        *(userns_args or []),
    ]
    if extra_args:
        argv += extra_args
    argv += ["-v", f"{worktree}:/workspace"]
    if refs is not None:
        argv += ["-v", f"{refs}:/refs:ro"]
    argv += ["-w", "/workspace"]
    if harness == "opencode2":
        argv += ["-i"]   # the brief arrives on stdin (see _opencode2_cmd)
    argv += auth_args
    argv += [image]
    argv += ["timeout", str(caps.timeout_s)]
    argv += _in_cage_cmd(harness, cli, brief, model, effort, broker_base)
    return argv


def _in_cage_cmd(
    harness: str, cli: str | None, brief: str, model: str | None, effort: str | None,
    broker_base: str | None = None,
) -> list[str]:
    if harness == "claude":
        return _claude_cmd(cli or "claude", brief, model, effort) + [
            "--dangerously-skip-permissions",
        ]
    if harness == "codex":
        # `broker_base` (broker path only) routes codex at the broker via a `-c`
        # provider override; None (the mount path) leaves the codex argv as-is.
        cmd = _codex_cmd(cli or "codex", brief, model, effort, broker_base=broker_base)
        return [
            "--dangerously-bypass-approvals-and-sandbox" if tok == "--approve-for-me" else tok
            for tok in cmd
        ]
    if harness == "opencode2":
        return _opencode2_cmd(cli or "opencode2", model, effort)
    raise ValueError(f"unknown harness: {harness!r}")


def _mutator_container_name(
    run_id: str | None, worktree_name: str, parent_name: str | None = None,
) -> str:
    """`nethackers-mut-${RUN_ID}-${ITER}` (spec §3.3), prefixed per INV13 so
    every container we start is ``docker ps -f name=nethackers-``-filterable.
    Falls back to ``parent_name`` when no run id is available (existing unit
    tests / callers that don't yet have one) -- see ``ContainerOperator.run``'s
    caller for why the run id is preferred over the worktree-derived form."""
    stem = run_id if run_id is not None else parent_name
    return f"nethackers-mut-{stem}-{worktree_name}"


class ContainerOperator:
    """A drop-in operator backend alongside ``operator.ClaudeOperator`` /
    ``CodexOperator`` -- same interface (``run(worktree, brief, *, on_line,
    stop)``), same ``OperatorResult`` contract -- that runs the chosen
    ``harness`` inside a resource-capped container instead of on the host.

    Streaming and metering are reused wholesale via ``run_operator``; only
    two things differ from the host path: the argv is ``build_docker_argv``'s
    ``docker run …`` wrapper instead of a bare harness command, and ``stop``
    must additionally ``docker kill`` the named container -- SIGKILLing the
    local ``docker run`` client (what ``run_operator``'s own stop-watcher
    already does) leaves the daemon-managed container running, since the
    client is only a foreground attach to it.
    """

    def __init__(
        self,
        *,
        harness: str,
        image: str,
        cli: str | None = None,
        model: str | None = None,
        effort: str | None = None,
        caps: ContainerCaps = _DEFAULT_CAPS,
        system: str | None = None,
        home: Path | None = None,
        docker: str = "docker",
        run_id: str | None = None,
        broker: bool = False,
        # `Callable[..., _CredBrokerLike]`, not a narrower
        # `Callable[[str, HeaderRewrite], _CredBrokerLike]`: real
        # `cred_broker.CredBroker` (the default) takes a third `impersonate`
        # keyword (`_start_broker_auth` passes it for codex), and this
        # mirrors `broker_credential` just below, an existing injectable
        # seam with the identical "real signature has more than the two
        # tests need to fake" shape.
        cred_broker_factory: Callable[..., _CredBrokerLike] = CredBroker,
        broker_credential: Callable[..., HeaderRewrite] = _default_broker_credential,
        userns_args: list[str] | None = None,
    ) -> None:
        self.harness = harness
        self.image = image
        self.cli = cli
        self.model = model
        self.effort = effort
        self.caps = caps
        # Threaded into the container name (see `run`) so it satisfies spec
        # §3.3's `nethackers-mut-${RUN_ID}-${ITER}` -- `worktree.parent.name`
        # alone is ALWAYS the literal "work" (worktree == <workdir>/runs/<rid>/
        # work/iter-N), so without this every run would collide on the same
        # name (e.g. `nethackers-mut-work-iter-0`), racing on `docker run
        # --name` and letting a hard-stop's `docker kill` hit the wrong run's
        # live container.
        # None keeps the old worktree-derived form as a fallback, so callers
        # that don't (yet) have a run id -- e.g. existing unit tests -- still
        # work; every real caller (launch.py) always passes one.
        self._run_id = run_id
        # Resolved once at construction (not per-run): callers that care
        # about the real host (production) leave these unset; tests inject a
        # fake system/home so the auth preflight below never touches the
        # actual filesystem or Keychain.
        self.system = system if system is not None else platform.system()
        self.home = home if home is not None else Path.home()
        self.docker = docker
        # Resolved by the CALLER (launch.py, via containers.nonroot_userns_args)
        # once per run, not probed here per iteration -- same "resolve once at
        # the entry point, thread it down" rule as `docker` itself, and it keeps
        # constructing an operator subprocess-free for tests. Empty on docker
        # and rootful podman.
        self.userns_args = list(userns_args or [])
        self._popen = subprocess.Popen
        # SELECTABLE broker path (§3d, INV2) -- default False keeps the
        # credential MOUNT (`auth_docker_args`, unchanged below) as what
        # every existing caller gets. `cred_broker_factory`/`broker_credential`
        # are injectable seams (mirrors `_popen` above) so a test can drive
        # the broker path without starting a real server or touching the
        # Keychain/`~/.codex`/`.credentials.json`.
        self.broker = broker
        self._cred_broker_factory = cred_broker_factory
        self._broker_credential = broker_credential
        self._run = subprocess.run

    def run(
        self,
        worktree: Path,
        brief: str,
        *,
        refs: Path | None = None,
        on_line: Callable[[str], None] | None = None,
        stop: threading.Event | None = None,
    ) -> OperatorResult:
        # `worktree.name` is already `iter-N`; prefer the run id (spec §3.3:
        # `nethackers-mut-${RUN_ID}-${ITER}`) so two runs never collide on the
        # same container name -- see the __init__ comment on `self._run_id`.
        name = _mutator_container_name(
            self._run_id, worktree.name, worktree.parent.name
        )
        # N brokers, not one: claude/codex ever start exactly one (their
        # single upstream/credential), but opencode2 -- multi-provider, with
        # a per-provider base-URL override rather than one env var -- starts
        # one PER BROKERABLE PROVIDER (see `_start_broker_auth`). Every entry
        # here gets `.stop()`ed in the `finally` below, regardless of harness
        # or how many there turned out to be (including zero).
        broker_procs: list[_CredBrokerLike] = []
        try:
            if self.broker:
                # Broker path (§3d, INV2): a placeholder key + base-URL(s)
                # instead of a credential mount. Broker(s) are started here,
                # before the docker argv is even built -- the auth args need
                # each broker's (host-gateway) base URL, which only exists
                # once `start()` has run. `broker_base` is codex-only (the
                # host-gateway URL its `-c` provider override needs baked into
                # the in-cage command); None for claude/opencode2, which route
                # via auth env / per-provider cage config instead.
                auth, extra_args, broker_base = self._start_broker_auth(broker_procs)
            else:
                # Resolve auth/config BEFORE shelling out to docker: login-only
                # backends fail early instead of mounting an empty path. OpenCode 2
                # never fails here (no key still leaves its free models), and never
                # reads the worktree's config: that tree is untrusted.
                # AuthUnavailable propagates to the caller (the CLI catches it).
                auth = auth_docker_args(
                    self.harness, system=self.system, home=self.home, _require_exists=True,
                )
                extra_args = None
                broker_base = None
            argv = build_docker_argv(
                harness=self.harness, image=self.image, name=name, worktree=worktree,
                cli=self.cli, model=self.model, effort=self.effort, caps=self.caps,
                auth_args=auth, brief=brief, refs=refs, docker=self.docker,
                extra_args=extra_args, userns_args=self.userns_args, broker_base=broker_base,
            )
            done = threading.Event()
            watcher: threading.Thread | None = None
            if stop is not None:
                def _watch() -> None:
                    # Poll the shared `stop` until THIS run finishes (local
                    # `done`) -- mirrors run_operator's own watcher shape so a
                    # normal completion never blocks on, or poisons, `stop`
                    # (shared across every iteration's run).
                    while not done.wait(timeout=0.1):
                        if stop.is_set():
                            self._maybe_kill_on_stop(name, stop)
                            return
                watcher = threading.Thread(target=_watch, daemon=True)
                watcher.start()
            try:
                # `stop` is also passed straight through so run_operator does its
                # own SIGKILL of the local `docker run` client (reaping the
                # foreground process + the "killed" bookkeeping below); the
                # watcher above is what actually stops the container.
                return run_operator(
                    argv, cwd=".", backend=self.harness, on_line=on_line, stop=stop,
                    popen=self._popen,
                    stdin_text=brief if self.harness == "opencode2" else None,
                )
            finally:
                done.set()
                if watcher is not None:
                    watcher.join(timeout=2)
                # Closes a race between the two watchers (this one, and
                # run_operator's own internal one -- both poll the same `stop`
                # but against different `done` events): if run_operator's own
                # watcher is the one that notices `stop` first, it SIGKILLs only
                # the local `docker run` client, run_operator returns fast, and
                # THIS `done` gets set -- possibly while our watcher is still
                # mid-wait, so its `done.wait()` returns via the "already set"
                # path without ever reaching the `stop.is_set()` check above,
                # and the container is left running (SIGKILLing the client does
                # NOT stop it). This final call is synchronous, on the main
                # thread, made only after run_operator is confirmed done --
                # independent of watcher poll-timing, so it can't lose the race.
                # `_maybe_kill_on_stop` already no-ops when `stop` was never set
                # (normal completion), and `_docker_kill` is idempotent/
                # best-effort, so a possible double-call (watcher + this) is
                # harmless.
                self._maybe_kill_on_stop(name, stop)
        finally:
            # Outer to the whole method (not just the run_operator try/
            # finally above) so every broker outlives the container for its
            # ENTIRE lifetime, independent of whether run_operator returned
            # normally, was stopped, or raised. Orthogonal to the
            # stop-watcher/`_maybe_kill_on_stop` dance above: that manages
            # the DOCKER CONTAINER's lifecycle against `stop`; this manages
            # each BROKER PROCESS's lifecycle against this method returning --
            # neither one's cleanup depends on the other's, and that stays
            # true for N brokers exactly as it did for one: this loop is the
            # only thing that changed to generalize it, nothing about the
            # nesting above.
            for proc in broker_procs:
                proc.stop()

    def _start_broker_auth(
        self, broker_procs: list[_CredBrokerLike],
    ) -> tuple[list[str], list[str] | None, str | None]:
        """Start whatever ``CredBroker``(s) this run's harness needs and
        return the ``(auth_args, extra_args, broker_base)`` triple ``run``
        splices into the docker argv -- the broker-path counterpart to the
        plain ``auth_docker_args`` call in ``run``'s ``else`` branch. Every
        broker started is appended to ``broker_procs`` (the caller's list) so
        its ``finally`` stops all of them, however many there turned out to be.

        ``broker_base`` (the third element) is the codex broker's host-gateway
        URL, needed by ``build_docker_argv`` to bake codex's ``-c`` provider
        override into the in-cage command; it is ``None`` for claude and
        opencode2, whose broker routing is entirely in ``auth_args`` (an
        ``ANTHROPIC_BASE_URL`` env / the per-provider cage config) rather than
        the codex command.

        claude/codex each have exactly one upstream/credential -- unchanged
        from before this method existed: one ``CredBroker``, one
        ``auth_broker_args`` call. opencode2 is multi-provider: its base-URL
        override is a per-provider JSON field, not one env var, so it starts
        one broker PER BROKERABLE PROVIDER (``auth_inject.opencode2_broker_targets``)
        and rewrites the cage config to point each at its own broker
        (``opencode2_broker_docker_args``) -- never ``auth_broker_args``,
        which stays claude/codex-only (see its docstring). A config with no
        brokerable provider at all (e.g. only ``{file:...}`` keys) starts
        zero brokers and falls back to the credential mount wholesale,
        rather than mount an empty broker config behind an unused
        ``--add-host``.

        codex's ``CredBroker`` (only) is constructed with ``impersonate=True``:
        its upstream, ``chatgpt.com``, sits behind Cloudflare JA3/TLS
        fingerprinting that 403s a plain forward, so that one broker forwards
        via ``curl_cffi`` Chrome impersonation instead of ``httpx`` (see
        ``cred_broker.CredBroker``'s docstring) -- this needs ``pip install
        curl_cffi`` on the host. claude and every opencode2 broker stay
        ``impersonate=False`` (the default) -- their upstreams aren't behind
        the same fingerprinting.
        """
        codex_broker_base: str | None = None
        # Where the broker(s) LISTEN so the sandbox can reach them: loopback on
        # macOS (Docker Desktop routes the container's host.docker.internal
        # there), the docker BRIDGE GATEWAY on native Linux (the container
        # reaches the host via that gateway, not loopback -- 127.0.0.1 was
        # unreachable from a Linux container). NEVER 0.0.0.0: that exposes the
        # credential-injecting proxy on the host's public interface. An
        # undeterminable gateway falls back to loopback, which fails LOUD on
        # Linux rather than silently exposing the broker.
        bind_host = "127.0.0.1"
        if self.system != "Darwin":
            bind_host = _bridge_gateway_ip(self.docker, run=self._run) or "127.0.0.1"
        if self.harness == "opencode2":
            targets = opencode2_broker_targets(home=self.home, environ=os.environ)
            if not targets:
                auth = auth_docker_args(
                    self.harness, system=self.system, home=self.home, _require_exists=True,
                )
                return auth, None, None
            broker_bases: dict[tuple[str, str], str] = {}
            for target in targets:
                proc = self._cred_broker_factory(
                    target["upstream"], target["rewrite"], bind_host=bind_host,
                )
                broker_procs.append(proc)
                broker_bases[(target["file"], target["name"])] = _host_gateway_url(proc.start())
            auth = opencode2_broker_docker_args(
                self.home, environ=os.environ, broker_bases=broker_bases,
            )
        else:
            rewrite = self._broker_credential(
                self.harness, system=self.system, home=self.home, run=self._run,
                environ=os.environ,
            )
            proc = self._cred_broker_factory(
                _broker_upstream_base(self.harness), rewrite,
                # Only codex's upstream (chatgpt.com) is Cloudflare-fronted
                # and JA3/TLS-fingerprinted; claude's isn't, so it stays on
                # the plain httpx forward (impersonate's default, False).
                impersonate=(self.harness == "codex"),
                bind_host=bind_host,
            )
            broker_procs.append(proc)
            broker_base = _host_gateway_url(proc.start())
            # `home=` is required for the codex cage: an EMPTY, writable
            # `~/.codex` dir + `CODEX_HOME` env (codex needs a writable
            # $CODEX_HOME for its app-server socket/state) -- no token, no
            # config crosses (routing is the `-c` override below; auth is
            # broker-injected). The claude branch ignores `home`.
            auth = auth_broker_args(self.harness, broker_base=broker_base, home=self.home)
            # codex's model endpoint is routed by a `-c model_providers.…`
            # INVOCATION override (it survives `codex exec --ignore-user-config`,
            # which discards ~/.codex/config.toml), so the broker's host-gateway
            # base URL must reach `build_docker_argv` too -- not just
            # `auth_broker_args`. claude routes via the ANTHROPIC_BASE_URL env
            # `auth_broker_args` already emits, so it needs nothing here.
            if self.harness == "codex":
                codex_broker_base = broker_base
        # The container needs a route to the host-side broker(s);
        # `host.docker.internal` only resolves with this on Linux docker
        # (Docker Desktop/macOS already provides it) -- one add-host serves
        # every broker above, since they all listen on loopback and share
        # the same host-gateway rewrite (`_host_gateway_url`).
        extra_args = ["--add-host", "host.docker.internal:host-gateway"]
        return auth, extra_args, codex_broker_base

    def _maybe_kill_on_stop(self, name: str, stop: threading.Event | None) -> None:
        """Single check-and-act step, kept separate from the watcher's poll
        loop so it is unit-drivable: if ``stop`` is set, ``docker kill`` the
        named container. ``run``'s background thread calls this every poll
        tick; tests call it once directly with an already-set ``stop``."""
        if stop is not None and stop.is_set():
            self._docker_kill(name)

    def _docker_kill(self, name: str) -> None:
        """``docker kill`` the named container. Best-effort: a container that
        already exited on its own (``--rm`` reaped it) makes this fail
        harmlessly, which is fine -- there is nothing left to stop."""
        with contextlib.suppress(Exception):
            subprocess.run([self.docker, "kill", name], capture_output=True)
