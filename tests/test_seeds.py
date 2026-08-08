from nethackers.arena.seeds import trajectory_spec, trajectory_specs


def test_seed_schedule_is_deterministic_and_unique() -> None:
    first = trajectory_specs("secret", "2026-08-09", 1024)
    second = trajectory_specs("secret", "2026-08-09", 1024)

    assert first == second
    assert [spec.trajectory_id for spec in first] == list(range(1024))
    assert len({(spec.core_seed, spec.display_seed, spec.level_seed) for spec in first}) == 1024
    assert first[17] == trajectory_spec("secret", "2026-08-09", 17)


def test_new_batch_id_or_secret_changes_seeds() -> None:
    original = trajectory_spec("secret-one", "2026-08-09", 0)
    assert original != trajectory_spec("secret-two", "2026-08-09", 0)
    assert original != trajectory_spec("secret-one", "2026-08-16", 0)
