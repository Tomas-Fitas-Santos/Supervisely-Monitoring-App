"""Recovery must preserve validated work without exposing partial inventory."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from annotatiathon.dataset_indexing import IndexProgress, read_progress
from annotatiathon.import_checkpoints import ImportCheckpoints


def test_progress_file_lock_cannot_abort_indexing_and_attempts_are_throttled(monkeypatch, tmp_path):
    ds = SimpleNamespace(root_path=tmp_path)
    progress = IndexProgress(ds)
    progress(force=True, files=10)
    original = Path.replace
    attempts = []
    clock = [10.0]
    monkeypatch.setattr("annotatiathon.dataset_indexing.time.monotonic", lambda: clock[0])
    def locked(path, target):
        attempts.append(path)
        raise PermissionError("Windows sharing violation")
    monkeypatch.setattr(Path, "replace", locked)
    progress(force=True, files=11)
    for number in range(12, 1000):
        progress(files=number)
    assert len(attempts) == 1
    assert read_progress(ds)["files"] == 10
    monkeypatch.setattr(Path, "replace", original)
    clock[0] += 2
    progress(files=1000)
    assert read_progress(ds)["files"] == 1000


def test_checkpoint_batches_survive_process_loss_and_torn_append(tmp_path):
    cache = ImportCheckpoints(tmp_path)
    path = tmp_path / "frame.jpg"
    path.write_bytes(b"source")
    calls = []
    def compute(source):
        calls.append(source)
        return (32, 24)
    # Distinct operation keys exercise a full batch without 250 test files.
    for number in range(251):
        cache.cached(str(number), path, compute)
    assert len(cache.pending) == 1
    with cache.path.open("a") as stream:
        stream.write('{"torn":')
    recovered = ImportCheckpoints(tmp_path)
    for number in range(251):
        assert recovered.cached(str(number), path, compute) == (32, 24)
    assert recovered.reused == 250
    assert len(calls) == 252  # Only the uncommitted tail was recomputed.
    recovered.flush()
    assert ImportCheckpoints(tmp_path).cached("250", path, compute) == (32, 24)
    assert len(calls) == 252
    path.write_bytes(b"changed")
    ImportCheckpoints(tmp_path).cached("0", path, compute)
    assert len(calls) == 253


def test_source_change_during_validation_is_never_checkpointed(tmp_path):
    cache = ImportCheckpoints(tmp_path)
    path = tmp_path / "frame.jpg"
    path.write_bytes(b"source")
    def changed(source):
        source.write_bytes(b"replacement")
        return (32, 24)
    with pytest.raises(ValueError, match="source_file_changed"):
        cache.cached("dimensions", path, changed)
    assert not cache.entries


def test_checkpoint_storage_failure_is_bounded_and_source_remains_retryable(monkeypatch, tmp_path):
    cache = ImportCheckpoints(tmp_path)
    cache.pending = {"frame": {"signature": [], "value": [32, 24]}}
    monkeypatch.setattr("annotatiathon.import_checkpoints.os.fsync",
                        lambda fd: (_ for _ in ()).throw(OSError("disk error")))
    cache.flush()
    assert not cache.pending
    assert cache.warning_logged
