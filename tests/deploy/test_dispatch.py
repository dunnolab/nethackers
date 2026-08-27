GOOD = "ghcr.io/dunnolab/nethackers-hub@sha256:" + "b"*64

def test_help_exits_zero_and_prints_usage(env):
    r = env.run(["--help"])
    assert r.returncode == 0
    assert "deploy" in r.stdout and "rollback" in r.stdout and "status" in r.stdout

def test_unknown_subcommand_errors(env):
    r = env.run(["frobnicate"])
    assert r.returncode != 0
    assert "unknown" in (r.stderr + r.stdout).lower()

def test_deploy_rejects_non_digest_ref(env):
    r = env.run(["deploy", "ghcr.io/dunnolab/nethackers-hub:latest"])
    assert r.returncode != 0
    assert "digest" in (r.stderr + r.stdout).lower()

def test_deploy_rejects_injection_via_ssh_original_command(env):
    # forced-command: attacker controls SSH_ORIGINAL_COMMAND. Must be validated, never eval'd.
    r = env.run([], ssh_original="deploy $(touch /tmp/pwned)")
    assert r.returncode != 0
    assert "$(touch" not in env.calls()  # never executed

def test_ssh_original_command_parses_good_ref(env):
    r = env.run([], stdin="", ssh_original=f"status")
    assert r.returncode == 0  # status runs; proves SSH_ORIGINAL_COMMAND dispatch works
