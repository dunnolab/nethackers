from nethackers.contracts.models import Atom
from nethackers.hub.store import Store
from nethackers.hub.views.verified import read_verified, verification_status

FP = "a" * 64
IMG = "img@sha256:x"
SEEDS = (4839201, 1029384)


def _store(tmp_path):
    s = Store(tmp_path / "h.db")
    s.init_schema()
    return s


def _atom(seed, identity="val-dwa-law-fem", prog=0.4):
    return Atom(
        solution_digest="sha256:s",
        owner="sam",
        tier="verified",
        identity=identity,
        seed=seed,
        progression=prog,
        milestone="Dlvl:3",
        ascended=False,
        status="completed",
        turns=1,
        steps=1,
        evaluator_image=IMG,
    )


def test_read_verified_aggregates_current_epoch(tmp_path):
    s = _store(tmp_path)
    s.insert_verified_atoms(
        [_atom(4839201, prog=0.4), _atom(1029384, prog=0.6)],
        secret_fingerprint=FP,
        verifier_token_fingerprint="t",
    )
    # a stale-secret row must NOT be counted
    s.insert_verified_atoms(
        [_atom(4839201, prog=1.0)],
        secret_fingerprint="c" * 64,
        verifier_token_fingerprint="t",
    )
    got = read_verified(s, secret_fingerprint=FP, seeds=SEEDS, evaluator_image=IMG)
    assert got["per_identity"]["val-dwa-law-fem"]["progression"] == 0.5


def test_verification_status_states(tmp_path):
    s = _store(tmp_path)
    assert (
        verification_status(
            s, "sha256:s", secret_fingerprint=FP, evaluator_image=IMG, seeds=SEEDS
        )["state"]
        == "not_attempted"
    )
    s.insert_verified_attempt(
        solution_digest="sha256:s",
        secret_fingerprint=FP,
        evaluator_image=IMG,
        verifier_token_fingerprint="t",
        status="failed",
        failure_kind="crashed",
        message="x",
        identities_done=0,
        at="t",
    )
    assert (
        verification_status(
            s, "sha256:s", secret_fingerprint=FP, evaluator_image=IMG, seeds=SEEDS
        )["state"]
        == "attempted"
    )
