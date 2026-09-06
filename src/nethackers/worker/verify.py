from __future__ import annotations

import tempfile
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from nethackers._image_pins import ARENA_IMAGE
from nethackers.arena.seeds import secret_fingerprint
from nethackers.contracts.models import ObjectiveSpec
from nethackers.eval.runner import eval_batch
from nethackers.hub.objectives import CATALOG, IDENTITIES
from nethackers.hubclient.pull import pull


def _now() -> str:
    return datetime.now(UTC).isoformat()


def verified_identity_spec(identity: str, seeds) -> ObjectiveSpec:
    base = CATALOG[identity]
    return replace(base, name=f"{identity}__verify",
                   batch=tuple((seed, identity) for seed in seeds))


def verify_program(client, token, config, reference, *, image=ARENA_IMAGE, pull_fn=pull,
                   eval_fn=eval_batch, now_fn=_now, identities=IDENTITIES) -> str:
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
                evidence = eval_fn(tree, spec, image, now=now_fn(), secret=secret)
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
