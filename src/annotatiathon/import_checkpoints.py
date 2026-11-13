"""Private, versioned validation receipts, independent of inventory transactions.

Append batches instead of replacing an open file (Windows bind mounts may deny
replacement). A torn final record is ignored; a retry revalidates only that batch.
Receipts are an optimization, never a published or partially valid inventory.
"""
import json
import logging
import os
from pathlib import Path
from threading import RLock

logger = logging.getLogger(__name__)


class ImportCheckpoints:
    batch_size = 250

    def __init__(self, root):
        self.path = Path(root) / "validation-checkpoints-v1.jsonl"
        self.entries = {}
        self.pending = {}
        self.reused = 0
        self.warning_logged = False
        self.lock = RLock()
        try:
            with self.path.open(encoding="utf-8") as stream:
                for line in stream:
                    try:
                        batch = json.loads(line)
                        if isinstance(batch, dict):
                            self.entries.update(batch)
                    except (ValueError, UnicodeError):
                        continue  # Process died during this append; recalculate it.
        except FileNotFoundError:
            pass
        except (OSError, UnicodeError):
            self._warn()

    def _warn(self):
        if not self.warning_logged:
            logger.warning("Validation checkpoint storage unavailable; retained source files remain retryable")
            self.warning_logged = True

    def flush(self):
        with self.lock:
            self._flush()

    def _flush(self):
        if not self.pending:
            return
        # A leading newline isolates a torn previous append from this batch.
        payload = "\n" + json.dumps(self.pending, separators=(",", ":")) + "\n"
        try:
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
        except OSError:
            self._warn()
        finally:
            self.pending.clear()  # Bound memory even if the volume becomes unwritable.

    def cached(self, kind, path, compute, *, stat_reader=None):
        path = Path(path)
        stat_reader = stat_reader or (lambda path: path.stat())
        stat = stat_reader(path)
        signature = [stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino, stat.st_dev]
        key = kind + ":" + str(path.absolute())
        with self.lock:
            previous = self.entries.get(key)
            if isinstance(previous, dict) and previous.get("signature") == signature:
                self.reused += 1
                return tuple(previous["value"])
        value = compute(path)
        after = stat_reader(path)
        if [after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_ino, after.st_dev] != signature:
            raise ValueError("source_file_changed_during_validation")
        with self.lock:
            self.entries[key] = self.pending[key] = {"signature": signature, "value": value}
            if len(self.pending) >= self.batch_size:
                self._flush()
        return value
