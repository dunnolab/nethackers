from nethackers.harness.brief import VOCABULARY, build_brief

IDS = ["val-hum-law-fem", "val-hum-neu-fem", "val-dwa-law-fem"]
PID = {"val-hum-law-fem": 0.146, "val-hum-neu-fem": 0.173, "val-dwa-law-fem": 0.185}


def _brief(**kw):
    base = dict(identities=IDS, per_identity=PID, overall=0.168, target=0.174,
                seeds_per_identity=15, training_seeds=list(range(15)))
    base.update(kw)
    return build_brief("val", IDS[0], **base)


def test_no_forbidden_jargon():
    b = _brief().lower()
    for bad in ("this run", "iteration", " cell", "map-elites", "champion",
                "dev ", "held-out", "weakest", "build"):
        assert bad not in b, bad


def test_nle_spelled_out_once_then_abbreviated():
    b = _brief()
    assert "NetHack Learning Environment (NLE)" in b


def test_vocabulary_is_invariant_constant():
    assert "**identity**" in VOCABULARY and "**seed**" in VOCABULARY
    assert "**score**" in VOCABULARY and "**overall**" in VOCABULARY
    assert "**focused change**" in VOCABULARY
    assert "one coherent idea" in VOCABULARY          # focused-change definition (⑫)
    # invariant "0" (score starts near 0) is fine; a real run-count would use
    # a nonzero digit -- ban those, not digits outright.
    assert not any(c in VOCABULARY for c in "123456789")
    assert VOCABULARY in _brief()                      # threaded verbatim


def test_goal_is_the_average():
    b = _brief().lower()
    assert "overall average" in b
    assert "all-rounder" in b and "not a specialist" in b


def test_scores_table_exact_rows_weakest_first():
    b = _brief()
    assert "| character | score |" in b
    # exact identity -> score mapping, and weakest-first ordering
    i_law = b.index("`val-hum-law-fem` | 0.146")
    i_neu = b.index("`val-hum-neu-fem` | 0.173")
    i_dwa = b.index("`val-dwa-law-fem` | 0.185")
    assert i_law < i_neu < i_dwa                       # ascending score order
    assert "Overall average now: 0.168 · target to beat: 0.174" in b


def test_scores_table_sorts_even_when_declared_order_is_not_ascending():
    # IDS/PID above happen to already be ascending, so a deleted sorted()
    # wouldn't be caught above -- declare a non-ascending order here so the
    # sort itself is what makes the rows come out weakest-first.
    ids = ["val-dwa-law-fem", "val-hum-law-fem", "val-hum-neu-fem"]
    b = build_brief("val", ids[0], identities=ids, per_identity=PID, overall=0.168,
                    target=0.174, seeds_per_identity=15, training_seeds=list(range(15)))
    i_law = b.index("`val-hum-law-fem` | 0.146")
    i_neu = b.index("`val-hum-neu-fem` | 0.173")
    i_dwa = b.index("`val-dwa-law-fem` | 0.185")
    assert i_law < i_neu < i_dwa                       # ascending score order


def test_whats_kept_present_and_average_framed():
    b = _brief()
    assert "## What's kept" in b
    assert "kept" in b.lower() and "discarded" in b.lower()
    assert "0.174" in b                                # beats the target


def test_per_seed_detail_has_count_and_path():
    b = _brief()
    assert "/refs/parent-eval.json" in b
    assert "45 = 3 identities × 15 seeds" in b         # ⑧ count, ⑥ don't-make-them-infer
    assert "cause_of_death" in b


def test_references_and_contract_and_antigaming():
    b = _brief()
    assert "/refs/CONTEXT.md" in b and "/workspace" in b
    assert "make_agent()" in b                          # contract line (regression guard)
    assert "seed fingerprint" in b.lower()              # anti-gaming #1
    assert "scorer/nle quirks" in b.lower()             # anti-gaming #2
    assert "hypothesis" in b.lower()


def test_measure_command_intact():
    b = _brief()
    assert "python -m nethackers.arena.run" in b
    assert "--evaluation-id local" in b and "--out" in b
    lo = b.lower()
    assert "foreground" in lo and "run_in_background" in b and "single-shot" in lo


def test_training_seeds_contiguous_range():
    assert "0–14" in _brief()                           # en-dash range branch


def test_training_seeds_noncontiguous_list():
    b = _brief(training_seeds=[3, 17, 42])
    assert "3, 17, 42" in b                             # comma-joined branch


def test_wiki_path_conditional():
    assert "/knowledge" not in _brief().lower()
    assert "/knowledge" in _brief(wiki_path="/knowledge/nethack").lower()


def test_partial_coverage_no_misleading_overall():
    # a cold-start parent measured on only one identity
    b = build_brief("val", IDS[0], identities=IDS,
                    per_identity={"val-hum-law-fem": 0.146}, overall=0.146,
                    target=0.174, seeds_per_identity=15, training_seeds=list(range(15)))
    assert "| `val-hum-neu-fem` | — |" in b             # unmeasured -> em-dash row
    assert "Overall average now" not in b               # not shown on partial coverage
    assert "target to beat: 0.174" in b


def test_single_identity_variant_de_jargoned():
    b = build_brief("val-dwa-law-fem", "val-dwa-law-fem", identities=None,
                    per_identity={"val-dwa-law-fem": 0.18}, overall=0.18,
                    training_seeds=[0, 1], seeds_per_identity=2)
    assert "3 different characters" not in b
    assert "val-dwa-law-fem" in b
    assert "objective" not in b.lower()                 # no internal 'objective' leak
    assert "0.18" in b                                  # its score is shown


def test_single_identity_variant_keeps_safety_and_refs_lines():
    # HOWTO/MEASURE/_references are shared with the multi-identity path, but
    # nothing guarded that on the identities=None branch specifically.
    b = build_brief("val-dwa-law-fem", "val-dwa-law-fem", identities=None,
                    per_identity={"val-dwa-law-fem": 0.18}, overall=0.18,
                    training_seeds=[0, 1], seeds_per_identity=2)
    lo = b.lower()
    assert "make_agent()" in b                          # contract line
    assert "seed fingerprint" in lo                      # anti-gaming #1
    assert "scorer/nle quirks" in lo                      # anti-gaming #2
    assert "foreground" in lo                             # synchronous-eval steer
    assert "/refs/CONTEXT.md" in b                        # references pointer
    assert "hypothesis" in lo                              # focused-change comment
