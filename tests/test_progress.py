from __future__ import annotations

import numpy as np

from nethackers.arena.progress import ACHIEVEMENTS, NetHackProgress


def _observation(
    *,
    depth: int = 1,
    experience_level: int = 1,
    dungeon_number: int = 0,
    level_number: int = 1,
    message: str = "",
    tty_text: str = "",
) -> dict[str, np.ndarray]:
    blstats = np.zeros(27, dtype=np.int64)
    blstats[12] = depth  # BL_DEPTH
    blstats[18] = experience_level  # BL_XP
    blstats[23] = dungeon_number  # BL_DUNGEON_NUMBER
    blstats[24] = level_number  # BL_LEVEL_NUMBER
    return {
        "blstats": blstats,
        "message": np.frombuffer(message.encode(), dtype=np.uint8),
        "tty_chars": np.frombuffer(tty_text.encode(), dtype=np.uint8),
    }


def test_progress_tracks_depth_and_experience_levels() -> None:
    progress = NetHackProgress()

    assert progress.update(_observation(depth=2, experience_level=2)) == ACHIEVEMENTS["Xp:2"]
    assert progress.highest_achievement == "Xp:2"

    assert progress.update(_observation(depth=4, experience_level=1)) == ACHIEVEMENTS["Dlvl:4"]
    assert progress.highest_achievement == "Dlvl:4"


def test_progress_detects_location_milestones_from_dungeon_state() -> None:
    # Quest home levels are dungeon 3, levels 1-5.
    quest = NetHackProgress()
    home = _observation(dungeon_number=3, level_number=3)
    assert quest.update(home) == ACHIEVEMENTS["Home 3"]
    assert quest.highest_achievement == "Home 3"

    # The Astral Plane is level 1 of the Elemental Planes (dungeon 7).
    astral = NetHackProgress()
    on_astral = _observation(dungeon_number=7, level_number=1)
    assert astral.update(on_astral) == ACHIEVEMENTS["Astral Plane"]
    assert astral.highest_achievement == "Astral Plane"


def test_progress_ignores_milestone_words_in_screen_text() -> None:
    """Regression: milestone names appearing in on-screen text -- floor graffiti,
    quest dialogue, farlook encyclopedia -- must not be credited. This is the
    Dlvl-4 -> 0.875 "Astral Plane" false positive."""
    progress = NetHackProgress()

    progression = progress.update(
        _observation(
            depth=4,
            dungeon_number=0,  # Dungeons of Doom -- not the Quest or the Planes
            level_number=4,
            message="You read in the dust: 'Snakes on the Astral Plane'",
            tty_text="Take the Amulet to the Astral Plane.   Welcome to Home 3",
        )
    )

    assert progression == ACHIEVEMENTS["Dlvl:4"]
    assert progress.highest_achievement == "Dlvl:4"


def test_progress_detects_ascension_from_info_only() -> None:
    ascended = NetHackProgress()
    assert ascended.update(_observation(), {"is_ascended": True}) == 1.0
    assert ascended.highest_achievement == "You ascend t"

    # Terminal ascension text alone, without info['is_ascended'], is not enough.
    text_only = NetHackProgress()
    assert text_only.update(_observation(tty_text="You ascend to demigod-hood!")) == 0.0
    assert text_only.highest_achievement is None
