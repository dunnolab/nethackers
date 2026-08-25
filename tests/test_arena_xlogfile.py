from pathlib import Path

from nethackers.arena.xlogfile import parse_death_cause, read_death_cause

_LINE = (
    "version=3.6.7\tpoints=0\tdeathlev=1\trole=Ran\trace=Elf\t"
    "name=Agent\tdeath=killed by a jackal\twhile=praying\tturns=83"
)


def test_parse_extracts_verbatim_death_string():
    assert parse_death_cause(_LINE, is_ascended=False) == "killed by a jackal"


def test_parse_returns_none_for_ascension_flag():
    line = _LINE.replace("death=killed by a jackal", "death=ascended to demigod-hood")
    assert parse_death_cause(line, is_ascended=True) is None


def test_parse_filters_quit_and_escaped():
    for value in ("quit", "escaped the dungeon", "ascended to demigod-hood"):
        line = _LINE.replace("death=killed by a jackal", f"death={value}")
        assert parse_death_cause(line, is_ascended=False) is None


def test_parse_keeps_dramatic_deaths():
    for value in ("starved to death", "petrified by a chickatrice",
                  "turned to slime by a green slime", "poisoned by a rotted newt corpse"):
        line = _LINE.replace("death=killed by a jackal", f"death={value}")
        assert parse_death_cause(line, is_ascended=False) == value


def test_parse_takes_last_record_and_handles_blanks():
    text = _LINE + "\n\n" + _LINE.replace("a jackal", "a gnome lord") + "\n"
    assert parse_death_cause(text, is_ascended=False) == "killed by a gnome lord"


def test_parse_returns_none_when_empty_or_no_death_field():
    assert parse_death_cause("", is_ascended=False) is None
    assert parse_death_cause("version=3.6.7\tturns=5", is_ascended=False) is None


def test_read_death_cause_reads_file(tmp_path: Path):
    (tmp_path / "xlogfile").write_text(_LINE + "\n")
    assert read_death_cause(tmp_path, is_ascended=False) == "killed by a jackal"


def test_read_death_cause_degrades_to_none(tmp_path: Path):
    assert read_death_cause(None, is_ascended=False) is None
    assert read_death_cause(tmp_path, is_ascended=False) is None  # no xlogfile present


def test_read_death_cause_survives_invalid_utf8(tmp_path):
    # A non-UTF-8 byte must degrade to None, never raise (the "never raise" invariant).
    (tmp_path / "xlogfile").write_bytes(b"death=killed by a n\xffewt\tturns=5\n")
    assert read_death_cause(tmp_path, is_ascended=False) is None
