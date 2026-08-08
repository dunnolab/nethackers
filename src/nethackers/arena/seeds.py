from __future__ import annotations

import hashlib
import hmac

from nethackers.contracts.models import TrajectorySpec

_MAX_SEED = (1 << 63) - 1


def secret_fingerprint(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def _seed_part(digest: bytes, offset: int) -> int:
    return int.from_bytes(digest[offset : offset + 8], "big") & _MAX_SEED


def trajectory_spec(secret: str, evaluation_id: str, trajectory_id: int) -> TrajectorySpec:
    if not secret:
        raise ValueError("evaluation secret must not be empty")
    if trajectory_id < 0:
        raise ValueError("trajectory_id must be non-negative")
    message = f"nethack-arena\0{evaluation_id}\0{trajectory_id}".encode()
    digest = hmac.new(secret.encode(), message, hashlib.sha256).digest()
    return TrajectorySpec(
        trajectory_id=trajectory_id,
        core_seed=_seed_part(digest, 0),
        display_seed=_seed_part(digest, 8),
        level_seed=_seed_part(digest, 16),
        bot_seed=_seed_part(digest, 24),
    )


def trajectory_specs(
    secret: str,
    evaluation_id: str,
    count: int,
) -> tuple[TrajectorySpec, ...]:
    if count <= 0:
        raise ValueError("trajectory count must be positive")
    return tuple(
        trajectory_spec(secret, evaluation_id, trajectory_id)
        for trajectory_id in range(count)
    )
