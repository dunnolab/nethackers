from __future__ import annotations

import numpy as np

from nethackers.arena.progress import ACHIEVEMENTS, NetHackProgress


def _observation(
    *,
    depth: int = 1,
    experience_level: int = 1,
    message: str = "",
    tty_text: str = "",
) -> dict[str, np.ndarray]:
    blstats = np.zeros(27, dtype=np.int64)
    blstats[12] = depth
    blstats[18] = experience_level
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


def test_progress_detects_textual_achievements_not_encoded_as_depth_or_xp() -> None:
    progress = NetHackProgress()

    assert progress.update(_observation(tty_text="Welcome to Home 3")) == ACHIEVEMENTS["Home 3"]
    assert progress.highest_achievement == "Home 3"

    assert progress.update(_observation(message="You arrive on the Astral Plane.")) == ACHIEVEMENTS[
        "Astral Plane"
    ]
    assert progress.highest_achievement == "Astral Plane"


def test_progress_detects_ascension_from_info_and_terminal_text() -> None:
    progress = NetHackProgress()

    assert progress.update(_observation(), {"is_ascended": True}) == 1.0
    assert progress.highest_achievement == "You ascend t"

    text_progress = NetHackProgress()
    assert text_progress.update(_observation(tty_text="You ascend to demigod-hood!")) == 1.0
    assert text_progress.highest_achievement == "You ascend t"
