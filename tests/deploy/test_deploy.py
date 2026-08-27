GOOD = "ghcr.io/dunnolab/nethackers-hub@sha256:" + "c"*64

def test_deploy_happy_path_sequences_calls(env):
    r = env.run(["deploy", GOOD, "--yes"], stdin="tok123")
    assert r.returncode == 0, r.stderr
    calls = env.calls()
    # order: login -> pull -> boot-check run -> compose up -> inspect (health) -> curl public
    assert "docker login" in calls
    assert f"docker pull {GOOD}" in calls
    assert "docker run" in calls          # pre-flip boot check
    assert "compose" in calls and "up" in calls
    assert "curl" in calls
    # hub.env now pins the new digest
    assert f"NETHACKERS_HUB_IMAGE={GOOD}" in env.hub_env.read_text()
    # history got a line
    assert env.history.read_text().strip() != ""

def test_deploy_empty_stdin_skips_login(env):
    r = env.run(["deploy", GOOD, "--yes"], stdin="")
    assert r.returncode == 0
    assert "docker login" not in env.calls()

def test_deploy_dry_run_changes_nothing(env):
    before = env.hub_env.read_text()
    r = env.run(["deploy", GOOD, "--yes", "--dry-run"], stdin="tok")
    assert r.returncode == 0
    assert env.hub_env.read_text() == before      # untouched
    assert "docker pull" not in env.calls()        # no real actions

def test_deploy_aborts_if_boot_check_fails(env):
    r = env.run(["deploy", GOOD, "--yes"], stdin="tok", rc_env={"RC_RUN": 1})
    assert r.returncode != 0
    # never flipped: hub.env still holds the old (a*64) digest
    assert "c"*64 not in env.hub_env.read_text()
