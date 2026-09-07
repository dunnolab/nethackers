from __future__ import annotations

import tempfile
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from nethackers._image_pins import ARENA_IMAGE
from nethackers.arena.seeds import secret_fingerprint
from nethackers.contracts.models import ObjectiveSpec
from nethackers.eval.runner import DEFAULT_MAX_PARALLEL_EVALS, eval_batch
from nethackers.hub.objectives import CATALOG, IDENTITIES
from nethackers.hubclient.pull import pull


def _now() -> str:
    return datetime.now(UTC).isoformat()


def verified_identity_spec(identity: str, seeds) -> ObjectiveSpec:
    base = CATALOG[identity]
    return replace(base, name=f"{identity}__verify",
                   batch=tuple((seed, identity) for seed in seeds))


def compute_hidden_baseline(client, token, config, tree, *, image=ARENA_IMAGE,
                            eval_fn=eval_batch, now_fn=_now, identities=IDENTITIES,
                            log=print,
                            max_parallel_evals=DEFAULT_MAX_PARALLEL_EVALS) -> str:
    """Compute AutoAscend's hidden-seed floor from a local ``tree`` and submit
    it per identity, returning ``"succeeded"``/``"failed"``.

    ``verify_program``'s sibling for the reference floor, with three
    deliberate differences. There is no ``pull_fn``: the floor is a local
    AutoAscend tree the operator points at, not a ``repo@commit`` -- the
    floor has no ``solutions`` row and must never get one. There is no
    attempt bookkeeping: ``verified_attempts`` is keyed by solution digest,
    which the floor doesn't have, so a failure surfaces as a non-zero exit
    and a log line instead. And it RESUMES: at ~1095 episodes this run takes
    hours, so identities the hub already holds a complete batch for (every
    hidden seed covered, this epoch) are skipped rather than recomputed.

    Partial coverage is not coverage -- an identity missing even one seed is
    recomputed in full, since a batch is submitted whole. Each identity is
    submitted as soon as it finishes, so a crash at identity 50 keeps the
    49 already banked.
    """
    secret = config["secret"]
    seeds = tuple(config["seeds"])
    fp = secret_fingerprint(secret)

    todo = [i for i in identities if i not in _covered_identities(client, len(seeds))]
    log(f"baseline: {len(todo)} identity/identities to compute "
        f"({len(identities) - len(todo)} already covered), {len(seeds)} hidden seeds each")

    for n, identity in enumerate(todo, start=1):
        spec = verified_identity_spec(identity, seeds)
        try:
            evidence = eval_fn(tree, spec, image, now=now_fn(), secret=secret,
                               max_parallel_evals=max_parallel_evals)
            client.post_verify_baseline(token, evidence=evidence.to_dict(),
                                        secret_fingerprint=fp)
        except Exception as e:  # the eval container or the hub submission failed
            log(f"baseline: {identity} failed ({n}/{len(todo)}): {str(e)[-500:]}")
            return "failed"
        log(f"baseline: {identity} done ({n}/{len(todo)})")

    return "succeeded"


def _covered_identities(client, per_identity_total: int) -> frozenset[str]:
    """Identities whose floor is already complete for the current epoch, read
    off the public overview. A hub too old to serve a ``baseline`` block, or
    one that errors, yields an empty set -- the run then recomputes
    everything, which is wasteful but never wrong."""
    try:
        baseline = client.get_verify_overview().get("baseline") or {}
        per_identity = baseline.get("per_identity") or {}
    except Exception:
        return frozenset()
    return frozenset(
        ident for ident, agg in per_identity.items()
        if agg.get("episodes", 0) >= per_identity_total
    )


def verify_program(client, token, config, reference, *, image=ARENA_IMAGE, pull_fn=pull,
                   eval_fn=eval_batch, now_fn=_now, identities=IDENTITIES,
                   max_parallel_evals=DEFAULT_MAX_PARALLEL_EVALS) -> str:
    secret = config["secret"]
    seeds = tuple(config["seeds"])
    fp = secret_fingerprint(secret)
    done = 0

    # failure_kind reflects the FAILURE SOURCE, not just "something raised":
    # build_failed/crashed are deterministic (hub._DETERMINISTIC drops the
    # program from candidates until the image re-pins), while infra_error
    # (a hub submission blip) is transient and must stay retry-eligible --
    # see spec Sec 6.
    def report(status, failure_kind, message):
        client.post_verify_attempt(token, reference=reference, evaluator_image=image,
                                   secret_fingerprint=fp, status=status, failure_kind=failure_kind,
                                   message=message, identities_done=done)

    def fail(failure_kind, e):
        report("failed", failure_kind, str(e)[-500:])
        return "failed"

    with tempfile.TemporaryDirectory() as td:
        try:
            tree = pull_fn(f"{reference['repo']}@{reference['commit']}", Path(td))
        except Exception as e:  # can't clone/fetch the program
            return fail("build_failed", e)

        for identity in identities:
            spec = verified_identity_spec(identity, seeds)
            try:
                evidence = eval_fn(tree, spec, image, now=now_fn(), secret=secret,
                                   max_parallel_evals=max_parallel_evals)
            except Exception as e:  # the eval container itself crashed
                return fail("crashed", e)
            try:
                client.post_verify(token, reference=reference, evidence=evidence.to_dict(),
                                   secret_fingerprint=fp)
            except Exception as e:  # hub submission error -- transient
                return fail("infra_error", e)
            done += 1

    report("succeeded", None, None)
    return "succeeded"
