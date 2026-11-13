"""Read mounted frames through pinned directories instead of resolving every path."""
import hashlib
import os
import stat
from contextlib import AbstractContextManager

from fastapi import HTTPException
from PIL import Image, UnidentifiedImageError


class SourceDirectory(AbstractContextManager):
    def __init__(self, root, directory):
        self.root, self.directory, self.fd = root, directory, None

    def __enter__(self):
        parts = self.directory.relative_to(self.root).parts
        if os.open in os.supports_dir_fd and hasattr(os, "O_NOFOLLOW"):
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            self.fd = os.open(self.root, flags)
            try:
                for part in parts:
                    next_fd = os.open(part, flags, dir_fd=self.fd)
                    os.close(self.fd)
                    self.fd = next_fd
            except BaseException:
                self.__exit__(None, None, None)
                raise
        elif self.directory.is_symlink() or not self.directory.resolve().is_relative_to(self.root):
            raise ValueError("unsafe_source_directory")
        return self

    def __exit__(self, *_args):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def stat(self, path):
        if path.parent != self.directory:
            raise ValueError("unsafe_source_file")
        info = (os.stat(path.name, dir_fd=self.fd, follow_symlinks=False)
                if self.fd is not None else path.lstat())
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("unsafe_source_file")
        if self.fd is None and not path.resolve().is_relative_to(self.root):
            raise ValueError("unsafe_source_file")
        return info

    def inspect(self, path, strict):
        if path.parent != self.directory:
            raise ValueError("unsafe_source_file")
        descriptor = (os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self.fd)
                      if self.fd is not None else os.open(path, os.O_RDONLY))
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError("unsafe_source_file")
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
            size = os.fstat(stream.fileno()).st_size
            width, height = None, None
            if strict:
                try:
                    stream.seek(0)
                    with Image.open(stream) as image:
                        width, height = image.size
                except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
                    raise HTTPException(422, f"Unreadable frame {path.name}.") from exc
        return digest, size, width, height
