"""Stable primary coverage and independent repeated-frame obligations."""
import pytest


@pytest.mark.parametrize("count", [1, 2, 29, 30, 31, 900])
def test_contiguous_blocks_preserve_every_source_frame(count):
    from annotatiathon.work_queue import sample_repeat_frames, split_video
    frames = [f"frame-{index}" for index in range(count)]
    blocks = split_video(frames)
    assert [frame for block in blocks for frame in block] == frames
    assert all(1 <= len(block) <= 30 for block in blocks)
    repeats = [sample_repeat_frames(block, seed="event-seed", block_key=str(index))
               for index, block in enumerate(blocks)]
    assert all(len(set(sample)) == min(2, len(block)) for block, sample in zip(blocks, repeats))
    assert repeats == [sample_repeat_frames(block, seed="event-seed", block_key=str(index))
                       for index, block in enumerate(blocks)]
    if count == 900:
        assert len(blocks) == 30
        assert sum(map(len, repeats)) == 60
        assert count + sum(map(len, repeats)) == 960


def test_blocks_reject_duplicate_ids_and_invalid_sizes():
    from annotatiathon.work_queue import split_video
    with pytest.raises(ValueError):
        split_video(["same-id", "same-id"])
    with pytest.raises(ValueError):
        split_video(["frame"], block_size=0)
    assert split_video([]) == []
