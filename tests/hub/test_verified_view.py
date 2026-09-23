from nethackers.contracts.models import Atom
from nethackers.hub.objectives import IDENTITIES
from nethackers.hub.store import Store
from nethackers.hub.views.verified import (
    count_verified_programs,
    read_verified,
    verification_status,
)

FP = "a" * 64
IMG = "img@sha256:x"
SEEDS = (4839201, 1029384)


def _store(tmp_path):
    s = Store(tmp_path / "h.db")
    s.init_schema()
    return s


def _atom(seed, identity="val-dwa-law-fem", prog=0.4, digest="sha256:s"):
    return Atom(
        solution_digest=digest,
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
        arena_major=1,
    )
    # a stale-secret row must NOT be counted
    s.insert_verified_atoms(
        [_atom(4839201, prog=1.0)],
        secret_fingerprint="c" * 64,
        verifier_token_fingerprint="t",
        arena_major=1,
    )
    got = read_verified(s, secret_fingerprint=FP, seeds=SEEDS, arena_major=1)
    assert got["per_identity"]["val-dwa-law-fem"]["progression"] == 0.5


def test_verification_status_states(tmp_path):
    s = _store(tmp_path)
    assert (
        verification_status(
            s, "sha256:s", secret_fingerprint=FP, arena_major=1, seeds=SEEDS
        )["state"]
        == "not_attempted"
    )
    s.insert_verified_attempt(
        solution_digest="sha256:s",
        secret_fingerprint=FP,
        evaluator_image=IMG,
        arena_major=1,
        verifier_token_fingerprint="t",
        status="failed",
        failure_kind="crashed",
        message="x",
        identities_done=0,
        at="t",
    )
    assert (
        verification_status(
            s, "sha256:s", secret_fingerprint=FP, arena_major=1, seeds=SEEDS
        )["state"]
        == "attempted"
    )


def _grid(seeds, *, digest, identities=IDENTITIES):
    """Every cell of one program's hidden grid: identities x seeds."""
    return [_atom(seed, identity=i, digest=digest) for i in identities for seed in seeds]


def _insert(store, atoms, *, fingerprint=FP, major=1):
    store.insert_verified_atoms(
        atoms, secret_fingerprint=fingerprint, verifier_token_fingerprint="t",
        arena_major=major,
    )


def test_count_verified_programs_counts_only_a_full_grid(tmp_path):
    # one program covers every cell; the other is a single cell short.
    s = _store(tmp_path)
    _insert(s, _grid(SEEDS, digest="sha256:full"))
    _insert(s, _grid(SEEDS, digest="sha256:short")[:-1])
    assert count_verified_programs(s, secret_fingerprint=FP, arena_major=1, seeds=SEEDS) == 1


def test_count_verified_programs_ignores_other_epochs(tmp_path):
    # a rotated secret and a bumped arena major each start a new scope: a full
    # grid measured in one is not a verified program in another.
    s = _store(tmp_path)
    _insert(s, _grid(SEEDS, digest="sha256:a"), fingerprint="c" * 64)
    _insert(s, _grid(SEEDS, digest="sha256:b"), major=2)
    assert count_verified_programs(s, secret_fingerprint=FP, arena_major=1, seeds=SEEDS) == 0


def test_count_verified_programs_drops_a_retired_seed(tmp_path):
    # the grid was completed on (a, b); the hub now runs (a, c). The program no
    # longer covers the live grid, so it is no longer verified.
    s = _store(tmp_path)
    _insert(s, _grid(SEEDS, digest="sha256:a"))
    assert count_verified_programs(
        s, secret_fingerprint=FP, arena_major=1, seeds=(SEEDS[0], 777)
    ) == 0


def test_count_verified_programs_ignores_off_catalog_identities(tmp_path):
    # 72 identities complete, the 73rd missing, padded back to a full ROW COUNT
    # with an identity the catalog does not contain -- counting rows alone would
    # call this verified.
    s = _store(tmp_path)
    rows = _grid(SEEDS, digest="sha256:a", identities=IDENTITIES[:-1])
    rows += [_atom(seed, identity="nosuch-identity", digest="sha256:a") for seed in SEEDS]
    _insert(s, rows)
    assert count_verified_programs(s, secret_fingerprint=FP, arena_major=1, seeds=SEEDS) == 0


def test_count_verified_programs_never_counts_the_baseline(tmp_path):
    # AutoAscend's floor is the reference, never a participant -- it lives in
    # its own table and must not show up in a count of verified PROGRAMS.
    s = _store(tmp_path)
    s.insert_verified_baseline_atoms(
        _grid(SEEDS, digest="sha256:autoascend"), secret_fingerprint=FP,
        verifier_token_fingerprint="t", arena_major=1,
    )
    assert count_verified_programs(s, secret_fingerprint=FP, arena_major=1, seeds=SEEDS) == 0
