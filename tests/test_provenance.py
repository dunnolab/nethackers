# tests/test_provenance.py
"""Per-run provenance (design 5.9): ``run.json`` records the resolved
*platform* digests of the images a run actually launches, plus the
in-container operator version -- an untrusted debugging breadcrumb
(INV1/INV7), never gated on. Both resolvers are injectable so these tests
never touch a real Docker daemon."""
import json

from nethackers.harness import launch
from nethackers.harness.launch import EvolveParams, _default_operator_version, prepare_evolve
from nethackers.harness.store import LocalTreeStore


def _seed(tmp_path):
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "nethackers.solution.json").write_text(
        '{"root": ".", "entrypoint": "bot.py"}')
    return seed


def test_omitted_image_digest_resolver_falls_back_to_the_real_default(tmp_path, monkeypatch):
    # design constraint: reuse eval.runner._default_image_digest (imported
    # into launch.py as _default_image_digest) as the actual fallback, not a
    # re-implementation, when no resolver is injected. The kwarg default is
    # `None` rather than `= _default_image_digest` specifically so this name
    # is looked up dynamically at call time -- proven here by monkeypatching
    # the name itself, the same mechanism conftest.py's autouse
    # `_hermetic_provenance` fixture relies on to keep the rest of the suite
    # hermetic.
    monkeypatch.setattr(launch, "_default_image_digest", lambda image: f"sentinel:{image}")
    seed = _seed(tmp_path)

    plan = prepare_evolve(
        EvolveParams(objective="val-dwa-law-fem", seed=str(seed),
                     workdir=str(tmp_path / "wd"), owner="dev", from_seed=True,
                     image="repo/arena:tag", mutator_image="repo/mutator:tag"),
        tree_store=LocalTreeStore(tmp_path / "store"),
    )

    cfg = json.loads((plan.run_dir / "run.json").read_text())
    assert cfg["arena_image_digest"] == "sentinel:repo/arena:tag"
    assert cfg["mutator_image_digest"] == "sentinel:repo/mutator:tag"


def test_records_injected_digests_operator_version_and_model(tmp_path):
    seed = _seed(tmp_path)

    def fake_digest(image: str) -> str:
        return f"sha256:fake-{image}"

    def fake_version(operator: str, image: str) -> str:
        return f"{operator}-9.9.9 ({image})"

    plan = prepare_evolve(
        EvolveParams(objective="val-dwa-law-fem", seed=str(seed), operator="claude",
                     model="claude-x", workdir=str(tmp_path / "wd"), owner="dev",
                     from_seed=True, image="repo/arena:tag",
                     mutator_image="repo/mutator:tag"),
        tree_store=LocalTreeStore(tmp_path / "store"),
        image_digest_resolver=fake_digest,
        operator_version_resolver=fake_version,
    )

    cfg = json.loads((plan.run_dir / "run.json").read_text())
    assert cfg["arena_image_digest"] == "sha256:fake-repo/arena:tag"
    assert cfg["mutator_image_digest"] == "sha256:fake-repo/mutator:tag"
    assert cfg["operator_version"] == "claude-9.9.9 (repo/mutator:tag)"
    assert cfg["model"] == "claude-x"
    # the raw (mutable) tags stay recorded too -- the digest is additive.
    assert cfg["image"] == "repo/arena:tag" and cfg["mutator_image"] == "repo/mutator:tag"


def test_resolver_failures_are_best_effort_and_never_block_the_run(tmp_path):
    seed = _seed(tmp_path)

    def raising_digest(image: str) -> str:
        raise RuntimeError("docker down")

    def raising_version(operator: str, image: str) -> str:
        raise RuntimeError("docker down")

    plan = prepare_evolve(
        EvolveParams(objective="val-dwa-law-fem", seed=str(seed),
                     workdir=str(tmp_path / "wd"), owner="dev", from_seed=True),
        tree_store=LocalTreeStore(tmp_path / "store"),
        image_digest_resolver=raising_digest,
        operator_version_resolver=raising_version,
    )

    # the plan is still fully usable -- provenance never gates run startup.
    assert plan.run_dir.exists()
    cfg = json.loads((plan.run_dir / "run.json").read_text())
    assert cfg["arena_image_digest"] is None
    assert cfg["mutator_image_digest"] is None
    assert cfg["operator_version"] is None


def test_default_operator_version_resolver_uses_discovery_detect_cli(monkeypatch):
    # Calls the _default_operator_version function object imported above
    # directly (captured at module-collection time, before any fixture has
    # run) rather than dereferencing `launch._default_operator_version`
    # through the module at call time -- conftest.py's autouse
    # `_hermetic_provenance` fixture stubs that module attribute suite-wide,
    # so going through `launch._default_operator_version(...)` here would
    # just observe the stub, not this function's real delegation to
    # discovery.detect_cli. The captured reference is immune to that later
    # attribute reassignment.
    from nethackers.harness.discovery import CliInfo

    calls = {}

    def fake_detect_cli(backend, *, image=None, **kw):
        calls["backend"] = backend
        calls["image"] = image
        return CliInfo(backend, True, "claude 9.9.9", True)

    monkeypatch.setattr(launch, "detect_cli", fake_detect_cli)

    result = _default_operator_version("claude", "repo/mutator:tag")

    assert result == "claude 9.9.9"
    assert calls == {"backend": "claude", "image": "repo/mutator:tag"}
