# tests/hub/test_register_id.py
# Follow the register-path test setup already used in tests/hub/test_api.py /
# test_register*.py (a fake git commit-checker + LocalStubAuth mapping the
# bearer token to a login). This test asserts only the two additive changes.
from nethackers.hub.ids import program_id
from nethackers.hub.validate import RegisterResult


def test_register_result_carries_program_id():
    # RegisterResult is constructed with the repo@commit solution_id; the
    # program_id field must be derivable from it.
    result = RegisterResult(solution_id="github.com/o/r@c", owner="sam",
                            objective="val-hum-neu-fem", atoms_inserted=3,
                            program_id=program_id("github.com/o/r@c"))
    assert result.program_id == program_id("github.com/o/r@c")
    assert result.program_id.startswith("prog_")
