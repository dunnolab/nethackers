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
    try:
        with tempfile.TemporaryDirectory() as td:
            tree = pull_fn(f"{reference['repo']}@{reference['commit']}", Path(td))
            for identity in identities:
                spec = verified_identity_spec(identity, seeds)
                evidence = eval_fn(tree, spec, image, now=now_fn(), secret=secret)
                client.post_verify(token, reference=reference, evidence=evidence.to_dict(),
                                   secret_fingerprint=fp)
                done += 1
    except Exception as e:  # program build/crash/hang -> visible failed attempt
        client.post_verify_attempt(token, reference=reference, evaluator_image=image,
                                   secret_fingerprint=fp, status="failed", failure_kind="crashed",
                                   message=str(e)[-500:], identities_done=done)
        return "failed"
    client.post_verify_attempt(token, reference=reference, evaluator_image=image,
                               secret_fingerprint=fp, status="succeeded", failure_kind=None,
                               message=None, identities_done=done)
    return "succeeded"
