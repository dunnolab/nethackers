"""``nethackers`` verifies TLS against the OS trust store as well as certifi's
bundle -- the store ``gh``, git and the browser already use.

Behind a TLS-inspecting corporate firewall, IT installs the firewall's CA in
the OS store; certifi never has it. httpx trusted certifi alone, so every hub
call failed CERTIFICATE_VERIFY_FAILED while everything else on the machine
worked, and an evolve run kept every win local-only. The end-to-end check (a
fake inspection CA in a container's OS store) can't run hermetically here;
this pins the contract it relies on.
"""
import httpx
import truststore

import nethackers.cli as C


def test_main_verifies_tls_against_the_os_trust_store(capsys):
    C.main(["--version", "-o", "json"])   # offline; every command enters here
    capsys.readouterr()
    # httpx builds a fresh context per client -- the one every hub, GitHub and
    # model-broker call gets. certifi's roots stay loaded on top of the OS store.
    assert isinstance(httpx.create_ssl_context(), truststore.SSLContext)
