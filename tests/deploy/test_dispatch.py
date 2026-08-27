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

def test_deploy_rejects_injection_via_ssh_original_command(env, tmp_path):
    # forced-command: attacker controls SSH_ORIGINAL_COMMAND. Must be validated, never eval'd.
    canary = tmp_path / "pwned"
    r = env.run([], ssh_original=f"deploy $(touch {canary})")
    assert r.returncode != 0
    assert not canary.exists()   # the payload never executed

def test_ssh_original_command_parses_good_ref(env):
    r = env.run([], stdin="", ssh_original="status")
    assert r.returncode == 0  # status runs; proves SSH_ORIGINAL_COMMAND dispatch works

def test_deploy_rejects_uppercase_hex_digest(env):
    ref = "ghcr.io/dunnolab/nethackers-hub@sha256:" + "A" * 64
    r = env.run(["deploy", ref])
    assert r.returncode != 0
    assert "hex" in (r.stderr + r.stdout).lower()  # rejected by validate_ref, not the placeholder
