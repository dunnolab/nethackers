from nethackers.sandbox_flags import offline_flags, online_flags

def test_offline_is_fully_sealed():
    f = offline_flags()
    for need in ["--network", "none", "--read-only", "--cap-drop", "ALL",
                 "no-new-privileges", "--pids-limit", "--memory", "--memory-swap",
                 "--cpus", "--user"]:
        assert need in f, need
    # memory-swap pinned equal to memory (no 2x via swap)
    assert f[f.index("--memory") + 1] == f[f.index("--memory-swap") + 1]
    # non-root
    assert f[f.index("--user") + 1] != "0" and ":" in f[f.index("--user") + 1]

def test_online_has_caps_but_no_network_none():
    f = online_flags()
    assert "--network" not in f          # caller adds the egress allowlist
    for need in ["--cap-drop", "no-new-privileges", "--pids-limit", "--memory", "--cpus"]:
        assert need in f
