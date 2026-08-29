from nethackers.harness import launch


def test_evolveparams_factories_resolve_not_none(tmp_path, monkeypatch):
    # In a non-repo cwd the factory must yield the pin, never None.
    monkeypatch.chdir(tmp_path)
    p = launch.EvolveParams(objective="val-dwa-law-fem", seed="s")
    assert p.image is not None and p.mutator_image is not None
    assert "@sha256:" in p.mutator_image  # non-repo → pin
