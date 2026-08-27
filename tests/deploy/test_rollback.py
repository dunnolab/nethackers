GOOD = "ghcr.io/dunnolab/nethackers-hub@sha256:" + "d"*64
OLD  = "ghcr.io/dunnolab/nethackers-hub@sha256:" + "a"*64  # the fixture's starting pin

def test_autorollback_when_public_url_unhealthy(env):
    r = env.run(["deploy", GOOD, "--yes"], stdin="tok",
                rc_env={"RC_CURL_PUBLIC": 1})   # live URL fails after flip
    assert r.returncode != 0
    # rolled back: hub.env restored to the previous digest, and re-upped
    assert OLD in env.hub_env.read_text()
    assert env.calls().count("compose") >= 2   # up (new) then up (rollback)

def test_autorollback_when_new_container_unhealthy(env):
    r = env.run(["deploy", GOOD, "--yes"], stdin="tok",
                rc_env={"INSPECT_OUT": "unhealthy", "RC_INSPECT": 0})
    assert r.returncode != 0
    assert OLD in env.hub_env.read_text()

def test_autorollback_when_flip_command_fails(env):
    r = env.run(["deploy", GOOD, "--yes"], stdin="tok", rc_env={"RC_UP": 1})
    assert r.returncode != 0
    assert OLD in env.hub_env.read_text()   # flip failed -> rolled back to previous digest

def test_rollback_subcommand_repins_previous(env):
    # seed a history line: deployed GOOD, previous was OLD
    env.history.write_text(f"2026-08-27T00:00:00Z\t{GOOD}\t{OLD}\tci\n")
    env.hub_env.write_text(f"NETHACKERS_CLIENT_ID=pub\nNETHACKERS_HUB_IMAGE={GOOD}\n")
    r = env.run(["rollback", "--yes"])
    assert r.returncode == 0, r.stderr
    assert OLD in env.hub_env.read_text()

def test_status_reports_current_ref(env):
    r = env.run(["status"])
    assert r.returncode == 0
    assert "nethackers-hub@sha256:" in r.stdout
