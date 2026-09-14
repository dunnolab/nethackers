from typing import Any

from fastapi.testclient import TestClient

from nethackers._image_pins import ARENA_IMAGE
from nethackers.arena.seeds import secret_fingerprint
from nethackers.contracts.models import Evidence, Objective, TrajectoryResult
from nethackers.hub.api import create_app
from nethackers.hub.auth import LocalStubAuth
from nethackers.hub.objectives import IDENTITIES
from nethackers.hub.store import Store
from nethackers.hub.verify import VerifierConfig

VTOKEN = "verifier-token-1"
CFG = VerifierConfig(tokens=frozenset({VTOKEN}), secret="hidden-key", seeds=(4839201, 1029384))
REPO = "github.com/sam/nethacker"
SHA = "a" * 40


def _app(tmp_path: Any, *, verifier=CFG) -> tuple[TestClient, Store]:
    store = Store(tmp_path / "h.db")
    store.init_schema()
    class _Git:
        def commit_exists(self, repo: str, sha: str) -> bool: return True
    app = create_app(store, LocalStubAuth({"t": "sam"}),
                     git_factory=lambda token: _Git(), verifier=verifier)
    return TestClient(app), store


def test_verify_config_returns_secret_and_seeds(tmp_path):
    client, _ = _app(tmp_path)
    r = client.get("/verify/config", headers={"Authorization": f"Bearer {VTOKEN}"})
    assert r.status_code == 200
    assert r.json() == {"secret": "hidden-key", "seeds": [4839201, 1029384]}


def test_verify_config_rejects_bad_token(tmp_path):
    client, _ = _app(tmp_path)
    assert client.get("/verify/config", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_verify_config_503_when_unconfigured(tmp_path):
    client, _ = _app(tmp_path, verifier=None)
    r = client.get("/verify/config", headers={"Authorization": f"Bearer {VTOKEN}"})
    assert r.status_code == 503


def _seed_solution(store):
    store.upsert_solution(f"{REPO}@{SHA}", repo=REPO, commit_sha=SHA, owner="sam",
                          root="bot", entrypoint="bot.py", registered_at="t")


def _evidence_dict(identity="val-dwa-law-fem"):
    results = tuple(
        TrajectoryResult(trajectory_id=sd, status="completed", progress=0.4, ascended=False,
                         steps=1, turns=1, max_depth=1, end_status="died", error=None,
                         wall_seconds=0.1, character=identity, milestone=None)
        for sd in CFG.seeds)
    return Evidence.from_results(
        solution_digest=f"{REPO}@{SHA}", objective=Objective(character=None, seed_set="v"),
        evaluator_image=ARENA_IMAGE, results=results, created_at="t").to_dict()


def test_post_verify_ok(tmp_path):
    client, store = _app(tmp_path)
    _seed_solution(store)
    body = {"reference": {"repo": REPO, "commit": SHA}, "evidence": _evidence_dict(),
            "secret_fingerprint": secret_fingerprint(CFG.secret)}
    r = client.post("/verify", json=body, headers={"Authorization": f"Bearer {VTOKEN}"})
    assert r.status_code == 200
    payload = r.json()
    assert payload["inserted"] == 2
    total = len(IDENTITIES) * len(CFG.seeds)
    done = len(CFG.seeds)  # one identity submitted, every seed of it covered
    assert payload["coverage"] == {"done": done, "total": total}
    assert payload["ignored"] == total - done
    assert len(store.iter_verified_atoms(solution_digest=f"{REPO}@{SHA}")) == 2


def test_post_verify_unknown_solution_404(tmp_path):
    client, _ = _app(tmp_path)
    body = {"reference": {"repo": REPO, "commit": SHA}, "evidence": _evidence_dict(),
            "secret_fingerprint": secret_fingerprint(CFG.secret)}
    r = client.post("/verify", json=body, headers={"Authorization": f"Bearer {VTOKEN}"})
    assert r.status_code == 404


def _verify_body():
    return {"reference": {"repo": REPO, "commit": SHA}, "evidence": {}, "secret_fingerprint": "x"}


def test_post_verify_rejects_bad_token(tmp_path):
    client, _ = _app(tmp_path)
    r = client.post("/verify", json=_verify_body(), headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_post_verify_503_when_unconfigured(tmp_path):
    client, _ = _app(tmp_path, verifier=None)
    r = client.post("/verify", json=_verify_body(),
                    headers={"Authorization": f"Bearer {VTOKEN}"})
    assert r.status_code == 503


def test_post_verify_attempt_records_failure(tmp_path):
    client, store = _app(tmp_path)
    body = {"reference": {"repo": REPO, "commit": SHA}, "evaluator_image": ARENA_IMAGE,
            "secret_fingerprint": secret_fingerprint(CFG.secret), "status": "failed",
            "failure_kind": "build_failed", "message": "clone failed", "identities_done": 0}
    r = client.post("/verify/attempts", json=body, headers={"Authorization": f"Bearer {VTOKEN}"})
    assert r.status_code == 200
    latest = store.latest_verified_attempt(f"{REPO}@{SHA}",
                secret_fingerprint=secret_fingerprint(CFG.secret), arena_major=1)
    assert latest["status"] == "failed" and latest["failure_kind"] == "build_failed"


def test_post_verify_attempt_400s_an_unclassified_image_and_writes_nothing(tmp_path):
    """This route could not fail before the arena major landed -- it stored
    whatever evaluator_image the caller reported. Now an image with no major
    is refused, because an attempt filed under a guessed one would let
    verify_candidates skip a program over a failure that never happened in
    that scope. The refusal must leave no half-written row behind it."""
    client, store = _app(tmp_path)
    body = {"reference": {"repo": REPO, "commit": SHA},
            "evaluator_image": "sha256:" + "1" * 64,     # a bare local image Id
            "secret_fingerprint": secret_fingerprint(CFG.secret), "status": "failed",
            "failure_kind": "build_failed", "message": "clone failed",
            "identities_done": 0}
    r = client.post("/verify/attempts", json=body, headers={"Authorization": f"Bearer {VTOKEN}"})
    assert r.status_code == 400
    assert "ParityMismatch" in r.json()["detail"]
    assert store._conn.execute(
        "SELECT COUNT(*) FROM verified_attempts").fetchone()[0] == 0


def test_a_failed_attempt_at_another_major_does_not_suppress_a_candidate(tmp_path):
    """The whole point of scoping attempts by major. A deterministic failure
    recorded under a DIFFERENT major says nothing about this board: the program
    was never tried here. Suppressing it would silently strand a program on the
    current major with no way to be re-offered, which is precisely what keying
    attempts by the image digest used to do on every re-pin."""
    client, store = _app(tmp_path)
    digest = "github.com/c/x@" + "c" * 40
    store.upsert_solution(digest, repo="github.com/c/x", commit_sha="c" * 40,
                          owner="c", root=".", entrypoint="bot.py", registered_at="t")
    store.insert_verified_attempt(
        solution_digest=digest, secret_fingerprint=secret_fingerprint(CFG.secret),
        evaluator_image=ARENA_IMAGE, arena_major=2,
        verifier_token_fingerprint="t", status="failed", failure_kind="build_failed",
        message="x", identities_done=0, at="t")
    r = client.get("/verify/candidates?limit=8", headers={"Authorization": f"Bearer {VTOKEN}"})
    assert r.status_code == 200
    assert "github.com/c/x" in [row["reference"]["repo"] for row in r.json()["rows"]]


def test_candidates_excludes_fully_covered_and_failed(tmp_path):
    client, store = _app(tmp_path)
    # program A: registered, no verified coverage -> candidate
    store.upsert_solution("github.com/a/x@" + "a" * 40, repo="github.com/a/x", commit_sha="a" * 40,
                          owner="a", root=".", entrypoint="bot.py", registered_at="t")
    # program B: deterministically failed -> excluded
    store.upsert_solution("github.com/b/x@" + "b" * 40, repo="github.com/b/x", commit_sha="b" * 40,
                          owner="b", root=".", entrypoint="bot.py", registered_at="t")
    store.insert_verified_attempt(solution_digest="github.com/b/x@" + "b" * 40,
        secret_fingerprint=secret_fingerprint(CFG.secret), evaluator_image=ARENA_IMAGE,
        arena_major=1,
        verifier_token_fingerprint="t", status="failed", failure_kind="build_failed",
        message="x", identities_done=0, at="t")
    r = client.get("/verify/candidates?limit=8", headers={"Authorization": f"Bearer {VTOKEN}"})
    assert r.status_code == 200
    ids = [row["reference"]["repo"] for row in r.json()["rows"]]
    assert "github.com/a/x" in ids and "github.com/b/x" not in ids
