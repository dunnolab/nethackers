from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

# Progression values adapted from nle-progress (MIT), which adapted the BALROG
# NetHack progression metric. Values estimate empirical ascension probability.
ACHIEVEMENTS: dict[str, float] = {
    "Dlvl:1": 0.0,
    "Dlvl:2": 0.015392269075361172,
    "Dlvl:3": 0.017539535798967013,
    "Dlvl:4": 0.021221378364235505,
    "Dlvl:5": 0.026482449457437766,
    "Dlvl:6": 0.035428686118173014,
    "Dlvl:7": 0.04846098359243788,
    "Dlvl:8": 0.0695812141543123,
    "Dlvl:9": 0.09770935198701912,
    "Dlvl:10": 0.12558597019922987,
    "Dlvl:11": 0.1612677822768481,
    "Dlvl:12": 0.20612854836308672,
    "Dlvl:13": 0.2566039887664476,
    "Dlvl:14": 0.29247245425293333,
    "Dlvl:15": 0.30881240110986263,
    "Dlvl:16": 0.3249071349317381,
    "Dlvl:17": 0.34007883702829594,
    "Dlvl:18": 0.3526455577544006,
    "Dlvl:19": 0.3654881095022441,
    "Dlvl:20": 0.37895667923256743,
    "Dlvl:21": 0.39293747679675045,
    "Dlvl:22": 0.40832255575976223,
    "Dlvl:23": 0.4258360748858015,
    "Dlvl:24": 0.445181408701678,
    "Dlvl:25": 0.46637631565303056,
    "Dlvl:26": 0.5067605633802816,
    "Dlvl:27": 0.5543572044866264,
    "Dlvl:28": 0.6015648510382184,
    "Dlvl:29": 0.6465049415992812,
    "Dlvl:30": 0.6913110342176086,
    "Dlvl:31": 0.7101094299371864,
    "Dlvl:32": 0.7176456494828894,
    "Dlvl:33": 0.7232124894378948,
    "Dlvl:34": 0.7284405900470092,
    "Dlvl:35": 0.7327761924174481,
    "Dlvl:36": 0.7368528917489855,
    "Dlvl:37": 0.7421458943978863,
    "Dlvl:38": 0.7471321695760599,
    "Dlvl:39": 0.753731343283582,
    "Dlvl:40": 0.7610398408061306,
    "Dlvl:41": 0.76740810314648,
    "Dlvl:42": 0.7725338491295938,
    "Dlvl:43": 0.7771626297577855,
    "Dlvl:44": 0.7812567949554251,
    "Dlvl:45": 0.7858986134802957,
    "Dlvl:46": 0.7890256738822022,
    "Dlvl:47": 0.7929718875502008,
    "Dlvl:48": 0.7967408445768355,
    "Dlvl:49": 0.8018467495718222,
    "Dlvl:50": 0.8067964442444019,
    "Astral Plane": 0.8745866562925501,
    "Home 1": 0.3661126311920611,
    "Home 2": 0.5461852317697947,
    "Home 3": 0.5493249297123614,
    "Home 4": 0.561057980620522,
    "Home 5": 0.5655729909652877,
    "Xp:1": 0.0,
    "Xp:2": 0.01847840456172601,
    "Xp:3": 0.02081163581974355,
    "Xp:4": 0.024160136550546978,
    "Xp:5": 0.029108986017138745,
    "Xp:6": 0.036887590648350246,
    "Xp:7": 0.0507583712345433,
    "Xp:8": 0.07453595273884014,
    "Xp:9": 0.11704996473565571,
    "Xp:10": 0.17909953770432294,
    "Xp:11": 0.25480449090205187,
    "Xp:12": 0.33264942385807844,
    "Xp:13": 0.41442590519821954,
    "Xp:14": 0.49403949403949404,
    "Xp:15": 0.5778823703813513,
    "Xp:16": 0.6261221999477034,
    "Xp:17": 0.656281764586298,
    "Xp:18": 0.6810218550418937,
    "Xp:19": 0.7015328516442704,
    "Xp:20": 0.7174494081710576,
    "Xp:21": 0.7286977843944303,
    "Xp:22": 0.7371004909327723,
    "Xp:23": 0.7443446629532795,
    "Xp:24": 0.7513449678519879,
    "Xp:25": 0.7585537128343045,
    "Xp:26": 0.7642151855635002,
    "Xp:27": 0.7703376822716808,
    "Xp:28": 0.7759810263044415,
    "Xp:29": 0.7783345263660224,
    "Xp:30": 0.7804561949196475,
    "You ascend t": 1.0,
}

# Stable indices into NLE's 27-element blstats (bottom-line statistics).
BL_DEPTH = 12
BL_XP = 18
BL_DUNGEON_NUMBER = 23
BL_LEVEL_NUMBER = 24

# Dungeon numbers are the 0-based load order in dat/dungeon.def: Dungeons of
# Doom=0, Gehennom=1, Gnomish Mines=2, Quest=3, Sokoban=4, Fort Ludios=5,
# Vlad's Tower=6, Elemental Planes=7. The Astral Plane is level 1 of the
# Elemental Planes: the endgame builds up, so with depth(astral)=-5 ..
# depth(earth)=-1 the astral level is dlevel 1 (the dummy surface is dlevel 6).
QUEST_DUNGEON_NUMBER = 3
ELEMENTAL_PLANES_DUNGEON_NUMBER = 7
ASTRAL_PLANE_LEVEL_NUMBER = 1
ASCENSION_ACHIEVEMENT = "You ascend t"


@dataclass
class NetHackProgress:
    progression: float = 0.0
    highest_achievement: str | None = None

    def update(
        self,
        observation: Mapping[str, Any],
        info: Mapping[str, Any] | None = None,
    ) -> float:
        """Update and return the best BALROG progress seen so far.

        Depth and experience level come from blstats. The location milestones
        (quest ``Home`` levels and the ``Astral Plane``) are detected from the
        authoritative dungeon-number / level-number in blstats -- mirroring
        NetHack's ``In_quest`` / ``In_endgame`` -- not from on-screen text.
        Scanning the message line or full tty for these names matched floor
        graffiti, quest dialogue, and farlook encyclopedia entries that merely
        mention these places, crediting e.g. ``Astral Plane`` (0.875) on Dlvl 4.
        Ascension comes from ``info['is_ascended']``.
        """
        blstats = observation["blstats"]
        self._record(f"Dlvl:{int(blstats[BL_DEPTH])}")
        self._record(f"Xp:{int(blstats[BL_XP])}")

        dungeon_number = int(blstats[BL_DUNGEON_NUMBER])
        level_number = int(blstats[BL_LEVEL_NUMBER])
        if dungeon_number == QUEST_DUNGEON_NUMBER and 1 <= level_number <= 5:
            self._record(f"Home {level_number}")
        if (
            dungeon_number == ELEMENTAL_PLANES_DUNGEON_NUMBER
            and level_number == ASTRAL_PLANE_LEVEL_NUMBER
        ):
            self._record("Astral Plane")

        if info is not None and info.get("is_ascended", False):
            self._record(ASCENSION_ACHIEVEMENT)

        return self.progression

    def _record(self, achievement: str) -> None:
        value = ACHIEVEMENTS.get(achievement)
        if value is not None and value > self.progression:
            self.progression = value
            self.highest_achievement = achievement
