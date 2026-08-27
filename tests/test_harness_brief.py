# tests/test_harness_brief.py
from nethackers.contracts.models import Evidence, Objective, TrajectoryResult
from nethackers.harness.brief import build_brief


def _evidence(mean, episodes, tally):
    ends = [end for end, count in tally.items() for _ in range(count)]
    ends += ["unknown"] * (episodes - len(ends))
    results = tuple(
        TrajectoryResult(trajectory_id=i, status="completed", progress=mean, ascended=False,
                         steps=1, turns=1, max_depth=1, end_status=e, error=None,
                         wall_seconds=0.1, character="val-dwa-law-fem", milestone=None)
        for i, e in enumerate(ends))
    return Evidence.from_results(
        solution_digest="sha256:x",
        objective=Objective(character="val-dwa-law-fem"),
        evaluator_image="img", results=results, created_at="t")


def test_brief_targets_progression_not_proxies():
    b = build_brief("ascend", "val-wiz", _evidence(mean=12.3, episodes=8, tally={"starved": 5}))
    assert "progression" in b.lower()
    assert "reach deeper" not in b and "survive longer" not in b   # old proxy line gone
    assert "objective 'ascend'" in b                               # objective_name is used
    assert "CONTEXT.md" in b                                       # mean/tally now live in /refs/
    assert "12.3" not in b and "starved" not in b                  # no baked-in parent numbers


def test_brief_has_antigaming_and_generalization_and_gate():
    b = build_brief("ascend", "val-wiz", _evidence(mean=1.0, episodes=4, tally={}))
    assert "held-out" in b.lower()
    assert "seed fingerprint" in b.lower() or "fingerprint" in b.lower()   # named exploit #1
    assert "scorer/nle quirks" in b.lower()                                # named exploit #2
    assert "make_agent" in b                                              # frozen-skeleton gate
    assert "hypothesis" in b.lower()                                      # focused-change comment


def test_wiki_line_is_conditional():
    # NOTE: the shrunk brief's NetHack preamble always mentions "reference"
    # solutions under /refs/ (Task A2), so conditionality is checked against
    # the wiki *path* itself rather than the generic word "reference".
    assert "/knowledge" not in build_brief("o", "c", _evidence(1, 1, {})).lower()
    b = build_brief("o", "c", _evidence(1, 1, {}), wiki_path="/knowledge/nethack")
    assert "/knowledge" in b.lower()


def test_training_seeds_render_when_given():
    b = build_brief("o", "c", _evidence(1, 1, {}), training_seeds=[3, 17, 42])
    assert "3, 17, 42" in b

def test_brief_pins_the_judges_eval_config():
    ev = Evidence(solution_digest="sha256:x", objective=Objective(character=None),
                  evaluator_image="img", tier="self-reported", results=(),
                  episodes=0, mean_progress=0.0, ascensions=0, created_at="t")
    b = build_brief("mon-hum-cha-fem", "mon-hum-cha-fem", ev, training_seeds=[0, 1, 2])
    # The mutator must reproduce the JUDGE's games, not invent an evaluation-id.
    assert "python -m nethackers.arena.run" in b
    assert "--evaluation-id local" in b   # explicit judge namespace, not "the default"
    assert "local" in b   # names the judge's default namespace


def test_brief_gives_a_runnable_eval_command():
    from nethackers.contracts.models import Evidence, Objective
    from nethackers.harness.brief import build_brief
    ev = Evidence(solution_digest="sha256:x", objective=Objective(character=None),
                  evaluator_image="img", tier="self-reported", results=(),
                  episodes=0, mean_progress=0.0, ascensions=0, created_at="t")
    b = build_brief("kni-hum-law-fem", "kni-hum-law-fem", ev, training_seeds=[0, 1])
    assert "--evaluation-id local" in b   # explicit, not "the default"
    assert "--out" in b                   # the required flag that was missing


# --- generalist (set) brief -----------------------------------------------

def test_set_brief_lists_builds_and_weakest_first():
    per_identity = {"wiz-elf-cha-mal": 0.5, "wiz-orc-cha-mal": 0.1, "wiz-gno-neu-fem": 0.3}
    text = build_brief(
        "wiz", "wiz-elf-cha-mal", _evidence(mean=0.3, episodes=3, tally={}),
        identities=list(per_identity), per_identity=per_identity, training_seeds=[0, 1, 2])
    assert "3 builds" in text or "3 identities" in text
    # weakest build appears before the strongest in the breakdown
    assert text.index("wiz-orc-cha-mal") < text.index("wiz-elf-cha-mal")
    assert "sample" in text.lower()   # the soft "don't roll every build" note

def test_set_brief_keeps_contract_line():
    per_identity = {"a": 0.1, "b": 0.2}
    text = build_brief("set", "a", _evidence(mean=0.1, episodes=2, tally={}),
                       identities=["a", "b"], per_identity=per_identity)
    assert "make_agent()" in text

def test_set_brief_has_heldout_and_antigaming_and_hypothesis():
    per_identity = {"a": 0.1, "b": 0.2}
    text = build_brief("set", "a", _evidence(mean=0.1, episodes=2, tally={}),
                       identities=["a", "b"], per_identity=per_identity)
    assert "held-out" in text.lower()
    assert "fingerprint" in text.lower()          # named exploit #1
    assert "scorer/nle quirks" in text.lower()    # named exploit #2
    assert "hypothesis" in text.lower()           # focused-change comment

def test_single_identity_brief_unaffected_by_new_kwargs_when_absent():
    # identities=None (default) must still take the original, single-build path.
    b = build_brief("ascend", "val-wiz", _evidence(mean=1.0, episodes=4, tally={}))
    assert "a set of" not in b.lower()

# --- shrunk brief: framing + /refs/ pointer (both branches) --------------

def test_both_branches_frame_nethack_and_point_to_refs():
    single = build_brief("val-dwa-law-fem", "val-dwa-law-fem",
                         _evidence(mean=0.11, episodes=5, tally={"died": 5}))
    a_set = build_brief("mon", "mon-hum-law-mal",
                        _evidence(mean=0.10, episodes=5, tally={"died": 5}),
                        identities=["mon-hum-law-mal", "mon-hum-neu-mal", "mon-hum-cha-mal"])
    for b in (single, a_set):
        assert "nethack" in b.lower()          # frames the game (was missing in the set branch)
        assert "progression" in b.lower()      # what the metric is
        assert "/refs/" in b                    # points at the provisioned folders
        assert "/workspace" in b                # names the editable base
        assert "hypothesis" in b.lower()        # still asks for the focused-change comment


def test_brief_directs_synchronous_foreground_eval():
    # The mutator sandbox is single-shot; a backgrounded eval's result is lost
    # when the agent's turn ends (the claude-harness-in-mutator bug). The brief
    # must steer the agent to a foreground, waited-on eval instead.
    b = build_brief("ascend", "val-wiz", _evidence(mean=1.0, episodes=1, tally={}))
    lo = b.lower()
    assert "foreground" in lo
    assert "run_in_background" in b        # names the thing NOT to do
    assert "single-shot" in lo
