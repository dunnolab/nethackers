from fastapi.testclient import TestClient

from nethackers.contracts.models import Atom
from nethackers.hub.api import create_app
from nethackers.hub.auth import LocalStubAuth
from nethackers.hub.ids import program_id
from nethackers.hub.store import Store
from nethackers.hubclient import frontier
from nethackers.hubclient.client import HubClient

DIGEST = "github.com/vkurenkov/nethacker@503686e9ec14e098912850c2587dfab3123e759b"


def _client(tmp_path):
    store = Store(tmp_path / "h.sqlite3")
    store.init_schema()
    store.upsert_solution(DIGEST, repo="github.com/vkurenkov/nethacker",
                          commit_sha="503686e9ec14e098912850c2587dfab3123e759b",
                          owner="vkurenkov", root=".", entrypoint="bot.py",
                          registered_at="2026-08-24T00:00:00Z")
    return TestClient(create_app(store, LocalStubAuth({}))), store


def test_programs_list_enveloped(tmp_path):
    client, _ = _client(tmp_path)
    r = client.get("/programs")
    assert r.status_code == 200
    body = r.json()
    assert "generated_at" in body and isinstance(body["rows"], list)
    row = body["rows"][0]
    assert row["id"] == program_id(DIGEST)
    assert row["owner"] == "vkurenkov"
    assert row["reference"] == {"repo": "github.com/vkurenkov/nethacker",
                                "commit": "503686e9ec14e098912850c2587dfab3123e759b"}
    assert "root" not in row and "entrypoint" not in row


def test_program_get_by_opaque_id_and_404(tmp_path):
    client, _ = _client(tmp_path)
    pid = program_id(DIGEST)
    got = client.get(f"/programs/{pid}")
    assert got.status_code == 200
    assert got.json()["id"] == pid
    assert client.get("/programs/prog_missing").status_code == 404


def _seed_atoms(store, digest, identity="wiz-elf-cha-mal"):
    store.insert_atoms([
        Atom(solution_digest=digest, owner="vkurenkov", tier="self-reported",
             identity=identity, seed=0, progression=0.5, milestone=None,
             ascended=False, status="completed", turns=5, steps=10,
             evaluator_image="img@sha256:x")])


def test_program_identities_enveloped(tmp_path):
    client, store = _client(tmp_path)
    _seed_atoms(store, DIGEST)
    pid = program_id(DIGEST)
    r = client.get(f"/programs/{pid}/identities")
    assert r.status_code == 200
    body = r.json()
    assert body["program_id"] == pid
    assert body["rows"] == [{"identity": "wiz-elf-cha-mal", "progression": 0.5, "episodes": 1}]
    assert client.get("/programs/prog_missing/identities").status_code == 404


def test_program_regime_end_to_end_champion_to_identities(tmp_path):
    """Task-5 forward-carry closure. ``frontier.champion()`` already returns
    a ``program_id`` (Task 1's ``/board`` migration). Before this task,
    ``champion_scores()`` still called the retired ``solution_frontier()``
    (-> ``/solutions/{digest}/frontier``), which 404s on a ``prog_`` id --
    the TUI Frontier "Program" regime and the CLI ``frontier --program``
    command were broken at runtime even though their own tests (client
    doubles) stayed green throughout Tasks 1-4.

    Wires a REAL ``HubClient`` through this file's real ``TestClient`` (an
    actual HTTP round trip -- no client double) and drives the exact chain
    those surfaces use: ``champion`` -> ``champion_scores`` ->
    ``program_identities`` -> ``GET /programs/{id}/identities``. Also
    confirms the retired path really would have 404'd on this id, so the
    fix is provably load-bearing, not just cosmetic."""
    client, store = _client(tmp_path)
    _seed_atoms(store, DIGEST, identity="wiz-elf-cha-mal")
    hub = HubClient("", http=client)

    champ = frontier.champion(hub)
    assert champ is not None
    pid, owner = champ
    assert pid == program_id(DIGEST)
    assert owner == "vkurenkov"

    scores = frontier.champion_scores(hub, pid)
    assert scores == {"wiz-elf-cha-mal": 0.5}

    # The bug this closes: the retired route only ever understood a real
    # solution digest, never an opaque program_id.
    assert client.get(f"/solutions/{pid}/frontier").status_code == 404


def _seed_owner(store, owner, n, *, start=0):
    """``n`` distinct programs for ``owner`` -- enough to page past a limit."""
    for i in range(start, start + n):
        commit = f"{i:040x}"
        store.upsert_solution(f"github.com/{owner}/bot@{commit}",
                              repo=f"github.com/{owner}/bot", commit_sha=commit,
                              owner=owner, root=".", entrypoint="bot.py",
                              registered_at=f"2026-08-24T00:00:{i % 60:02d}Z")


def test_programs_envelope_reports_true_total_not_page_length(tmp_path):
    """The website's hacker popup prints "registered programs" from this
    response. It read the PAGE LENGTH, so every hacker past the page size was
    reported as exactly the page size (prod: vkurenkov's 237 programs shown as
    50). A page cannot know the size of the set it came from -- so the envelope
    carries the honest ``total`` for the same filter, independent of paging."""
    client, store = _client(tmp_path)
    _seed_owner(store, "cinemere", 106)
    _seed_owner(store, "Luab", 7)

    page = client.get("/programs?owner=cinemere&limit=50").json()
    assert len(page["rows"]) == 50           # still one page
    assert page["total"] == 106              # ...of a set this big

    assert client.get("/programs?owner=Luab&limit=50").json()["total"] == 7
    # unfiltered total counts every owner (106 + 7 + the fixture's 1)
    assert client.get("/programs?limit=10").json()["total"] == 114
    # the total is a property of the SET, not of the page: a short tail page
    # (6 rows past offset 100) still reports 106
    tail = client.get("/programs?owner=cinemere&limit=50&offset=100").json()
    assert len(tail["rows"]) == 6 and tail["total"] == 106
    assert client.get("/programs?owner=nobody&limit=50").json()["total"] == 0
