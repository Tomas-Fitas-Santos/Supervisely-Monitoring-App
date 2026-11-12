"""Bounded real Supervisely job creation/deletion timing, with strict isolation.

THIS IS OPT-IN. It creates ONE (up to three) jobs on a named, existing,
disposable benchmark project using an existing image. Never uploads files,
modifies annotations, or touches ordinary Nightjar event jobs.

Execute inside the live worker container only with --allow-create-delete
and explicit IDs of a benchmark dataset, benchmark image, and test member.
"""
import argparse
import json
import os
import time
from pathlib import Path
from statistics import mean
from uuid import uuid4

from .db import session_factory
from .models import SuperviselyConfig
from .native_worker import connect_native
from .supervisely_probe import percentile

TEST_PROJECT_PREFIX = "nightjar benchmark"


def validate_test_resources(api, config, *, dataset_id: int,
                            image_id: int, user_id: int) -> None:
    """All identifiers must refer to a dedicated preexisting benchmark project."""
    workspace = api.workspace.get_info_by_id(config.workspace_id)
    dataset = api.dataset.get_info_by_id(dataset_id)
    image = api.image.get_info_by_id(image_id)
    project = api.project.get_info_by_id(dataset.project_id) if dataset else None
    if (workspace is None or workspace.team_id != config.team_id
            or dataset is None or image is None or project is None):
        raise ValueError("Benchmark workspace, dataset, image or project is missing.")
    if not project.name.casefold().startswith(TEST_PROJECT_PREFIX):
        raise ValueError("Refusing writes outside a 'Nightjar Benchmark' project.")
    if project.workspace_id != config.workspace_id or image.dataset_id != dataset_id:
        raise ValueError("Benchmark image/dataset/workspace mismatch.")
    if user_id <= 0:
        raise ValueError("A verified test account's numeric Supervisely user ID is required.")


def matching_jobs(api, config, *, dataset_id: int, title: str) -> list:
    """Recovery after an ambiguous create response uses the unique run title."""
    return [
        job for job in api.labeling_job.get_list(
            config.team_id, dataset_id=dataset_id, show_disabled=True,
        ) if job.name == title
    ]


def create_delete_once(api, config, *, dataset_id: int,
                       image_id: int, user_id: int, iteration: int) -> dict:
    """Record one real write roundtrip. Clean up even after an uncertain response."""
    title = f"Nightjar Benchmark latency probe {uuid4().hex}"
    created_ids: set[int] = set()
    creation_ms = None
    delete_ms = None
    verified = False
    failure = None
    started = time.perf_counter()
    try:
        # Refuse a collision instead of risking deletion of a pre-existing job.
        if matching_jobs(api, config, dataset_id=dataset_id, title=title):
            raise RuntimeError("Unexpected benchmark job title collision.")
        created = api.labeling_job.create(
            name=title, dataset_id=dataset_id, user_ids=[user_id],
            images_ids=[image_id],
            readme="Nightjar automated latency calibration. No annotation required.",
        )
        creation_ms = (time.perf_counter() - started) * 1000
        if len(created) != 1:
            raise RuntimeError("Expected exactly one new Supervisely labeling job.")
        created_ids.add(created[0].id)
        info = api.labeling_job.get_info_by_id(created[0].id)
        if (info is None or info.name != title or info.dataset_id != dataset_id
                or info.assigned_to_id != user_id):
            raise RuntimeError("The new job's identity could not be verified.")
    except Exception as exc:  # noqa: BLE001 - cleanup still mandatory
        failure = type(exc).__name__
    finally:
        # If create succeeded but its response was lost, recover the exact
        # unique title and remove only objects with matching benchmark identity.
        try:
            matches = matching_jobs(api, config, dataset_id=dataset_id, title=title)
            for item in matches:
                if (item.dataset_id != dataset_id or item.assigned_to_id != user_id
                        or item.id not in created_ids and item.name != title):
                    raise RuntimeError("Unrecognized job identity; cleanup aborted.")
                created_ids.add(item.id)
            if len(created_ids) > 1:
                raise RuntimeError("Ambiguous benchmark create: multiple jobs; check cleanup manually.")
            if created_ids:
                identifier = next(iter(created_ids))
                begun = time.perf_counter()
                api.labeling_job.remove(identifier)
                delete_ms = (time.perf_counter() - begun) * 1000
                for attempt in range(3):
                    if api.labeling_job.get_info_by_id(identifier) is None:
                        verified = True
                        break
                    time.sleep(1)
            else:
                # No matching object was created or left behind.
                verified = True
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                "Benchmark cleanup could not be confirmed. STOP TESTING and "
                "inspect only the dedicated 'Nightjar Benchmark' project."
            ) from exc
    if failure:
        raise RuntimeError(
            "Supervisely benchmark job create/verify failed; cleanup attempted "
            f"({failure}). Do not continue until the benchmark project is checked."
        )
    if not verified:
        raise RuntimeError("Benchmark job deletion not confirmed; do not repeat the test.")
    if creation_ms is None or delete_ms is None:
        raise RuntimeError("Incomplete create/delete timing sample.")
    return {
        "iteration": iteration,
        "publish_ms": round(creation_ms, 3),
        "delete_ms": round(delete_ms, 3),
        "removed_and_verified": True,
    }


def run(*, dataset_id: int, image_id: int,
        user_id: int, repeats: int, pause_seconds: float,
        api=None, config=None) -> tuple[dict, dict]:
    if not 1 <= repeats <= 3:
        raise ValueError("Only 1–3 tiny real create/delete samples are permitted.")
    if pause_seconds < 2:
        raise ValueError("At least two seconds between samples are required.")
    if api is None or config is None:
        with session_factory()() as db:
            config = db.get(SuperviselyConfig, 1)
            if (config is None or not config.verified or config.team_id is None
                    or config.workspace_id is None):
                raise ValueError("Verified Supervisely configuration is required.")
            api = connect_native(config)
    validate_test_resources(api, config, dataset_id=dataset_id,
                            image_id=image_id, user_id=user_id)
    samples = []
    for iteration in range(1, repeats + 1):
        if iteration > 1:
            time.sleep(pause_seconds)
        samples.append(create_delete_once(
            api, config, dataset_id=dataset_id, image_id=image_id,
            user_id=user_id, iteration=iteration,
        ))
    published = [item["publish_ms"] for item in samples]
    deleted = [item["delete_ms"] for item in samples]
    profile = {"publish": {
        "count": len(published), "p50_ms": percentile(published, .5),
        "p95_ms": percentile(published, .95),
        "p99_ms": percentile(published, .99),
        "mean_ms": round(mean(published), 3),
    }}
    summary = {
        "iterations": repeats,
        "real_jobs_created": repeats,
        "real_jobs_removed_verified": repeats,
        "real_images_uploaded": 0,
        "publish_p50_ms": profile["publish"]["p50_ms"],
        "publish_p95_ms": profile["publish"]["p95_ms"],
        "delete_p50_ms": percentile(deleted, .5),
        "delete_p95_ms": percentile(deleted, .95),
        "simulation_annotators": 300,
    }
    return summary, profile


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-create-delete", action="store_true",
                        help="Explicitly permit test-job writes in a dedicated benchmark project.")
    parser.add_argument("--dataset-id", type=int, required=True)
    parser.add_argument("--image-id", type=int, required=True)
    parser.add_argument("--test-user-id", type=int, required=True)
    parser.add_argument("--repeats", type=int, default=1, choices=(1, 2, 3))
    parser.add_argument("--pause-seconds", type=float, default=3.0)
    parser.add_argument("--output", type=Path,
                        default=Path("/data/metrics/benchmark-calibrated.json"))
    args = parser.parse_args()
    if not args.allow_create_delete:
        parser.error("No remote writes permitted without --allow-create-delete.")
    if (not args.output.is_absolute()
            or not args.output.is_relative_to("/data/metrics")):
        parser.error("Use an absolute profile path under /data/metrics.")
    try:
        summary, profile = run(
            dataset_id=args.dataset_id, image_id=args.image_id,
            user_id=args.test_user_id, repeats=args.repeats,
            pause_seconds=args.pause_seconds,
        )
    except Exception as exc:  # noqa: BLE001 - omit response contents/credentials
        parser.exit(1, f"Benchmark job probe aborted: {type(exc).__name__}. "
                       "Inspect the dedicated test project for possible leftover jobs.\n")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Combine read-only live timings and the single real create/delete
    # measurement. The isolated benchmark reads this ONE aggregate profile.
    read_profile = Path("/data/metrics/read-only-profile.json")
    merged = json.loads(read_profile.read_text(encoding="utf-8")) if read_profile.exists() else {}
    if not isinstance(merged, dict):
        raise ValueError("Invalid existing read-only timing profile.")
    merged.update(profile)
    args.output.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    os.chmod(args.output, 0o600)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
