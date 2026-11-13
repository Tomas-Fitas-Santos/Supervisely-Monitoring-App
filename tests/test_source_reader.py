import hashlib
import os

import pytest

from annotatiathon.source_reader import SourceDirectory


def test_pinned_directory_blocks_leaf_and_parent_symlinks(tmp_path):
    if os.open not in os.supports_dir_fd:
        pytest.skip("requires openat directory handles")
    directory = tmp_path / "species" / "clip"
    directory.mkdir(parents=True)
    original = directory / "frame.jpg"
    original.write_bytes(b"original")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "frame.jpg").write_bytes(b"replacement")
    with SourceDirectory(tmp_path, directory) as reader:
        (directory / "link.jpg").symlink_to(outside / "frame.jpg")
        with pytest.raises(ValueError, match="unsafe_source_file"):
            reader.stat(directory / "link.jpg")
        with pytest.raises(OSError):
            reader.inspect(directory / "link.jpg", False)
        # Renaming/swapping the parent cannot redirect an already pinned reader.
        directory.rename(directory.with_name("retained"))
        directory.symlink_to(outside, target_is_directory=True)
        assert reader.inspect(original, False)[:2] == (hashlib.sha256(b"original").hexdigest(), 8)
    assert reader.fd is None
    with pytest.raises(OSError), SourceDirectory(tmp_path, directory):
        pass


def test_source_reader_rejects_paths_outside_its_directory(tmp_path):
    directory = tmp_path / "clip"
    directory.mkdir()
    with SourceDirectory(tmp_path, directory) as reader:
        for operation in (reader.stat, lambda path: reader.inspect(path, False)):
            with pytest.raises(ValueError, match="unsafe_source_file"):
                operation(tmp_path / "elsewhere.jpg")
