"""Synthetic local-ingest benchmark; no live database, media, or Supervisely writes.

Hard-linked JPEG fixtures represent logical source bytes with little disk usage.
This measures application overhead with cached source bytes, NOT disk/network
throughput. Run on the actual source mount separately to measure its I/O cost.
"""
import argparse
import hashlib
import io
import json
import os
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from annotatiathon import mounted_datasets
from annotatiathon.dataset_indexing import IndexProgress
from annotatiathon.import_checkpoints import ImportCheckpoints
from annotatiathon.models import Base, Event, MediaDataset, StaffAccount


def benchmark(images: int, image_bytes: int, videos: int = 1):
    with tempfile.TemporaryDirectory(prefix="nightjar-ingest-benchmark-") as directory:
        root = Path(directory)
        sources = root / "sources"
        species = sources / "synthetic" / "species"
        folders = [species / f"clip_{number:04d}" for number in range(videos)]
        for folder in folders:
            folder.mkdir(parents=True)
        encoded = io.BytesIO()
        Image.new("RGB", (32, 24)).save(encoded, format="JPEG")
        content = encoded.getvalue()
        payload = content + bytes(max(0, image_bytes - len(content)))
        started = time.perf_counter()
        for number in range(1, images + 1):
            # ext4 limits links to one inode; rotate small seed files.
            if number % 10_000 == 1:
                sample = root / f"sample-{number}.jpg"
                sample.write_bytes(payload)
            os.link(sample, folders[min((number - 1) * videos // images, videos - 1)]
                    / f"frame_{number:08d}.jpg")
        setup_seconds = time.perf_counter() - started
        engine = create_engine(f"sqlite:///{root / 'metadata.sqlite'}")
        Base.metadata.create_all(engine)
        timings = {"checksum_worker_seconds": 0.0, "image_header_worker_seconds": 0.0}

        def measured(name, function):
            def call(*args, **kwargs):
                start = time.perf_counter()
                try:
                    return function(*args, **kwargs)
                finally:
                    timings[name] += time.perf_counter() - start
            return call

        settings = SimpleNamespace(
            nightjar_dataset_source_dir=str(sources), max_dataset_assets=250_000,
            max_dataset_bytes=140_000_000_000,
        )
        with Session(engine, expire_on_commit=False) as db:
            event = Event(name="Synthetic ingest benchmark")
            staff = StaffAccount(name="Synthetic benchmark", email="benchmark@example.invalid",
                                 roles=["organiser"], password_hash="disabled")
            db.add_all([event, staff])
            db.flush()
            ds = MediaDataset(event_id=event.id, name="Synthetic", status="indexing",
                upload_kind="mounted", source_folder="synthetic", extraction_policy="all_frames",
                root_path=str(root / "private"), created_by=staff.id)
            (Path(ds.root_path) / "media").mkdir(parents=True)
            db.add(ds)
            db.commit()
            start = time.perf_counter()
            with (
                patch.object(mounted_datasets, "get_settings", return_value=settings),
                patch.object(hashlib, "file_digest",
                    measured("checksum_worker_seconds", hashlib.file_digest)),
                patch.object(Image, "open", measured("image_header_worker_seconds", Image.open)),
            ):
                progress = IndexProgress(ds)
                progress.checkpoint = ImportCheckpoints(ds.root_path)
                mounted_datasets.index_mounted_dataset(db, ds, progress)
                db.commit()
                progress.checkpoint.flush()
            elapsed = time.perf_counter() - start
            result = {
                "synthetic": True, "database": "isolated SQLite",
                "source_storage": "hard links; cached repeated JPEG bytes",
                "images": ds.frame_count, "logical_source_bytes": ds.source_bytes,
                "videos": ds.video_count,
                "setup_seconds": round(setup_seconds, 3),
                "index_seconds": round(elapsed, 3),
                "images_per_second": round(images / elapsed, 1),
                **{key: round(value, 3) for key, value in timings.items()},
                "supervisely_upload_tested": False,
                "timing_note": "Worker times overlap; do not subtract their sum from wall time.",
            }
        engine.dispose()
        return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--images", type=int, default=100_000)
    parser.add_argument("--image-bytes", type=int, default=65_536)
    parser.add_argument("--videos", type=int, default=1)
    options = parser.parse_args()
    if (not 1 <= options.images <= 250_000 or not 0 <= options.image_bytes <= 1024**2
            or not 1 <= options.videos <= options.images):
        parser.error("Use 1..250000 images, 0..1048576 bytes per fixture, and 1..images videos")
    print(json.dumps(benchmark(options.images, options.image_bytes, options.videos), indent=2))
