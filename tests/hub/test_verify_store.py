from nethackers.contracts.models import Atom
from nethackers.hub.store import Store

FP = "a" * 64
TOKFP = "b" * 64
IMG = "img@sha256:x"


def _atom(**kw):
    base = dict(solution_digest="sha256:s", owner="sam", tier="verified",
                identity="val-dwa-law-fem", seed=4839201, progression=0.5, milestone=None,
                ascended=False, status="completed", turns=1, steps=1, evaluator_image=IMG)
    base.update(kw)
    return Atom(**base)


def _store(tmp_path):
    s = Store(tmp_path / "h.db"); s.init_schema(); return s


def test_insert_verified_atoms_dedups_on_unique_key(tmp_path):
    s = _store(tmp_path)
    atoms = [_atom(seed=1), _atom(seed=2)]
    assert s.insert_verified_atoms(atoms, secret_fingerprint=FP, verifier_token_fingerprint=TOKFP) == 2
    assert s.insert_verified_atoms(atoms, secret_fingerprint=FP, verifier_token_fingerprint=TOKFP) == 0
    # verified_atoms are NOT in the self-reported table
    assert s.iter_atoms() == []


def test_rotation_new_secret_fingerprint_does_not_collide(tmp_path):
    s = _store(tmp_path)
    s.insert_verified_atoms([_atom(seed=1)], secret_fingerprint=FP, verifier_token_fingerprint=TOKFP)
    s.insert_verified_atoms([_atom(seed=1)], secret_fingerprint="c" * 64, verifier_token_fingerprint=TOKFP)
    assert len(s.iter_verified_atoms(solution_digest="sha256:s")) == 2


def test_iter_verified_atoms_filters(tmp_path):
    s = _store(tmp_path)
    s.insert_verified_atoms([_atom(seed=1, identity="val-dwa-law-fem"),
                             _atom(seed=1, identity="wiz-elf-cha-fem")],
                            secret_fingerprint=FP, verifier_token_fingerprint=TOKFP)
    got = s.iter_verified_atoms(secret_fingerprint=FP, evaluator_image=IMG, identity="val-dwa-law-fem")
    assert len(got) == 1 and isinstance(got[0], Atom) and got[0].ascended is False


def test_verified_attempt_latest_wins(tmp_path):
    s = _store(tmp_path)
    s.insert_verified_attempt(solution_digest="sha256:s", secret_fingerprint=FP, evaluator_image=IMG,
                              verifier_token_fingerprint=TOKFP, status="failed",
                              failure_kind="build_failed", message="boom", identities_done=0, at="t1")
    s.insert_verified_attempt(solution_digest="sha256:s", secret_fingerprint=FP, evaluator_image=IMG,
                              verifier_token_fingerprint=TOKFP, status="succeeded",
                              failure_kind=None, message=None, identities_done=73, at="t2")
    latest = s.latest_verified_attempt("sha256:s", secret_fingerprint=FP, evaluator_image=IMG)
    assert latest["status"] == "succeeded" and latest["identities_done"] == 73
    assert s.latest_verified_attempt("sha256:none", secret_fingerprint=FP, evaluator_image=IMG) is None
