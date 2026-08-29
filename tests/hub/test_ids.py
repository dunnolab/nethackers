from nethackers.hub.ids import program_id


def test_program_id_is_deterministic_prefixed_and_128_bit():
    ref = "github.com/vkurenkov/nethacker@503686e9ec14e098912850c2587dfab3123e759b"
    pid = program_id(ref)
    assert pid == program_id(ref)                 # deterministic / idempotent
    assert pid.startswith("prog_")
    assert len(pid) == len("prog_") + 32          # 128-bit truncation, 32 hex
    assert all(c in "0123456789abcdef" for c in pid.removeprefix("prog_"))
    assert program_id(ref) != program_id(ref + "x")   # collision-resistant on distinct refs
    assert "/" not in pid                          # slash-free (kills the {digest:path} bug class)
