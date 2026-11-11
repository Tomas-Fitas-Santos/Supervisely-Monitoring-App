"""Pure primary-block construction for the versioned event queue."""
import hashlib


def split_video(ordered_asset_ids: list[str], block_size: int = 30) -> list[list[str]]:
    """Split one video's ordered canonical IDs without crossing video boundaries."""
    if block_size < 1:
        raise ValueError("Block size must be positive")
    if len(set(ordered_asset_ids)) != len(ordered_asset_ids):
        raise ValueError("A source frame must occur once in its video's primary coverage")
    return [ordered_asset_ids[start:start + block_size]
            for start in range(0, len(ordered_asset_ids), block_size)]


def sample_repeat_frames(block: list[str], *, seed: str, block_key: str) -> list[str]:
    """Draw at most two distinct canonical frames, reproducibly across retries."""
    ranked = sorted(block, key=lambda asset_id: (
        hashlib.sha256(f"{seed}\0{block_key}\0{asset_id}".encode()).digest(), asset_id,
    ))
    if len(set(block)) != len(block):
        raise ValueError("Repeat candidates must have distinct canonical IDs")
    return ranked[:2]
