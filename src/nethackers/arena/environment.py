from __future__ import annotations

import importlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from nethackers.arena.progress import NetHackProgress
from nethackers.contracts.models import TrajectorySpec

PUBLIC_OBSERVATION_KEYS = (
    "glyphs",
    "chars",
    "colors",
    "specials",
    "blstats",
    "message",
    "inv_glyphs",
    "inv_strs",
    "inv_letters",
    "inv_oclasses",
    "tty_chars",
    "tty_colors",
    "tty_cursor",
    "misc",
)


def disable_autopickup(options: tuple[str, ...]) -> tuple[str, ...]:
    """Return NLE NetHack options with only autopickup switched off."""
    filtered = [option for option in options if option not in {"autopickup", "!autopickup"}]
    return ("!autopickup", *filtered)


@dataclass(frozen=True)
class EnvironmentMetrics:
    progress: float
    turns: int
    max_depth: int
    ascended: bool
    end_status: str | None
    milestone: str | None


class NLEEnvironment:
    """Deterministic NetHackChallenge, optionally restricted to a fixed character build.

    ``character`` follows NLE's ``role-race-alignment-gender`` string format (e.g.
    ``"val-dwa-law-fem"``). When ``None``, the ``character`` kwarg is omitted from the
    underlying ``NetHackChallenge`` so NLE performs its natural random draw.
    """

    def __init__(
        self, max_steps: int, no_progress_timeout: int, character: str | None = None
    ) -> None:
        try:
            nethack = importlib.import_module("nle.nethack")
            NetHackChallenge = importlib.import_module("nle.env.tasks").NetHackChallenge
        except ImportError as error:
            raise RuntimeError("NLE is not installed; run `uv sync --extra nle`") from error

        nethack_options = disable_autopickup(tuple(nethack.NETHACKOPTIONS))

        challenge_kwargs = dict(
            observation_keys=PUBLIC_OBSERVATION_KEYS,
            options=nethack_options,
            allow_all_yn_questions=True,
            allow_all_modes=True,
            max_episode_steps=max_steps,
            no_progress_timeout=no_progress_timeout,
            fix_moon_phase=True,
        )
        if character is not None:
            challenge_kwargs["character"] = character

        # NetHackChallenge is resolved via importlib (see try/except above) so this
        # module stays importable without NLE installed; mypy cannot statically
        # resolve a base class computed at runtime.
        class DeterministicChallenge(NetHackChallenge):  # type: ignore[valid-type,misc]
            def __init__(self) -> None:
                super().__init__(**challenge_kwargs)
                self.options = nethack_options
                if tuple(self.actions) != tuple(nethack.ACTIONS):
                    raise RuntimeError(
                        "NetHackChallenge action table does not match nethack.ACTIONS"
                    )

            def install_seeds(self, spec: TrajectorySpec) -> None:
                # NetHackChallenge blocks agent-controlled seeding. The trusted judge
                # installs its deterministic seeds directly before reset.
                type(self.nethack).set_initial_seeds(
                    self.nethack,
                    spec.core_seed,
                    spec.display_seed,
                    False,
                    spec.level_seed,
                )

        self._nethack = nethack
        self._env = DeterministicChallenge()
        self.action_count = int(self._env.action_space.n)
        self._progress = NetHackProgress()
        self._metrics = EnvironmentMetrics(0.0, 0, 1, False, None, None)

    def reset(self, spec: TrajectorySpec) -> Mapping[str, Any]:
        self._env.install_seeds(spec)
        self._progress = NetHackProgress()
        observation, info = self._env.reset()
        self._update_metrics(observation, info)
        return observation

    def step(self, action: int) -> tuple[Mapping[str, Any], float, bool, bool]:
        observation, reward, terminated, truncated, info = self._env.step(action)
        self._update_metrics(observation, info)
        return observation, float(reward), bool(terminated), bool(truncated)

    def _update_metrics(self, observation: Mapping[str, Any], info: Mapping[str, Any]) -> None:
        blstats = observation["blstats"]
        self._metrics = EnvironmentMetrics(
            progress=self._progress.update(observation, info),
            turns=max(self._metrics.turns, int(blstats[self._nethack.NLE_BL_TIME])),
            max_depth=max(
                self._metrics.max_depth,
                int(blstats[self._nethack.NLE_BL_DLEVEL]),
            ),
            ascended=self._metrics.ascended or bool(info.get("is_ascended", False)),
            end_status=str(info["end_status"]) if info.get("end_status") is not None else None,
            # self._progress.update(...) above (bound to `progress=`) records the
            # achievement as a side effect before this line runs -- kwargs are
            # evaluated left-to-right -- so highest_achievement is already current.
            milestone=self._progress.highest_achievement,
        )

    def metrics(self) -> EnvironmentMetrics:
        return self._metrics

    def close(self) -> None:
        self._env.close()


def make_environment(
    max_steps: int, no_progress_timeout: int, character: str | None = None
) -> NLEEnvironment:
    return NLEEnvironment(
        max_steps=max_steps, no_progress_timeout=no_progress_timeout, character=character
    )
