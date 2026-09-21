from nethackers.sandbox_flags import offline_flags, online_flags


def test_offline_is_fully_sealed():
    f = offline_flags(memory="4g", cpus=2, pids=256)
    for need in ["--network", "none", "--read-only", "--cap-drop", "ALL",
                 "no-new-privileges", "--pids-limit", "--memory", "--memory-swap",
                 "--cpu-quota", "--user"]:
        assert need in f, need
    # memory-swap pinned equal to memory (no 2x via swap)
    assert f[f.index("--memory") + 1] == f[f.index("--memory-swap") + 1]
    # non-root
    assert f[f.index("--user") + 1] != "0" and ":" in f[f.index("--user") + 1]

def test_online_has_caps_but_no_network_none():
    f = online_flags()
    assert "--network" not in f          # caller adds the egress allowlist
    for need in ["--cap-drop", "no-new-privileges", "--pids-limit", "--memory", "--cpu-quota"]:
        assert need in f

def test_the_cpu_cap_is_a_cfs_quota_so_it_may_exceed_the_host():
    # docker refuses `--cpus` above the daemon's CPU count ("Range of CPUs is
    # from 0.01 to 4.00, as there are only 4 CPUs available"), but a box sized
    # from its workload can ask for more cores than a small host has. The CFS
    # pair `--cpus` expands to carries no such check; a quota past the host's
    # cores simply never binds.
    f = offline_flags(memory="16g", cpus=8, pids=256)
    assert "--cpus" not in f
    assert f[f.index("--cpu-period") + 1] == "100000"
    assert f[f.index("--cpu-quota") + 1] == "800000"
