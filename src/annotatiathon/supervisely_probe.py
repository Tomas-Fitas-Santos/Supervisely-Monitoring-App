"""Fully automated, bounded, READ-ONLY calibration of live Supervisely latency.

Invoked explicitly inside the EXISTING live worker container:
    python -m annotatiathon.supervisely_probe --samples 3

The probe never calls a mutation endpoint or creates jobs/media. It reads
existing verified Team/Workspace and (when available) a few existing bound
job/annotation records. No passwords, URLs, user IDs, job IDs or bodies are
written to the output. The generated profile is suitable for the fully
isolated 300-annotator simulator.
"""
import argparse
import json
import math
import os
import time
from collections import defaultdict
from pathlib import Path
from statistics import mean

import httpx
from sqlalchemy import select

from .db import session_factory
from .models import NativeJobBinding, NativeMediaInstance, SuperviselyConfig
from .supervisely_client import NativeAPIError, _request_json, require_connection

# Explicit allowlist. In particular, no jobs.add, images.bulk.add,
# images.bulk.upload, members.editInfo, or job restart methods.
READ_ONLY_OPERATIONS = frozenset({
    "users.me", "teams.info", "workspaces.info", "jobs.info", "annotations.info",
})


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1)], 3)


def aggregate(samples: dict[str, list[float]]) -> dict:
    result = {}
    for name, values in sorted(samples.items()):
        if values:
            result[name] = {
                "count": len(values),
                "p50_ms": percentile(values, .50),
                "p95_ms": percentile(values, .95),
                "p99_ms": percentile(values, .99),
                "mean_ms": round(mean(values), 3),
            }
    return result


def planned_requests(config: SuperviselyConfig, bindings: list[int],
                     images: list[int], samples: int) -> list[tuple[str, str, dict]]:
    """Allow only confirmed read paths and strictly limit request count."""
    items = [
        ("lookup", "users.me", {}),
        ("lookup", "teams.info", {"id": config.team_id}),
        ("lookup", "workspaces.info", {"id": config.workspace_id}),
    ]
    items.extend(("status", "jobs.info", {"id": job})
                 for job in bindings[:samples])
    items.extend(("annotation", "annotations.info", {"imageId": image})
                 for image in images[:samples])
    assert all(operation in READ_ONLY_OPERATIONS for _, operation, _ in items)
    return items


def calibrate(*, samples: int, min_interval: float) -> dict:
    if not 1 <= samples <= 4:
        raise ValueError("At most 4 existing jobs and 4 existing images can be probed.")
    if min_interval < 1:
        raise ValueError("Use a minimum interval of one second between live requests.")

    with session_factory()() as db:
        config = db.get(SuperviselyConfig, 1)
        if (config is None or not config.verified
                or not config.team_id or not config.workspace_id):
            raise ValueError("Verify Supervisely team and workspace in Nightjar Settings first.")
        base, token = require_connection(config)
        # Deterministic bounded selection of already-created real resources.
        bindings = list(db.scalars(
            select(NativeJobBinding.native_job_id)
            .order_by(NativeJobBinding.id).limit(samples),
        ))
        images = list(db.scalars(
            select(NativeMediaInstance.native_image_id)
            .order_by(NativeMediaInstance.id).limit(samples),
        ))
        plan = planned_requests(config, bindings, images, samples)

    successful: dict[str, list[float]] = defaultdict(list)
    results: dict[str, int] = defaultdict(int)
    # This one-off command is rate-limited and sequential. It does NOT
    # use the existing worker's publishing loop or run any native operations.
    with httpx.Client(timeout=httpx.Timeout(12, connect=5),
                      follow_redirects=False, trust_env=False) as client:
        for index, (group, endpoint, payload) in enumerate(plan):
            if index:
                time.sleep(min_interval)
            start = time.monotonic()
            try:
                _request_json(client, base, token, endpoint, payload)
            except NativeAPIError as exc:
                results[exc.code] += 1
                if exc.code in {"rate_limited", "unauthorised", "untrusted_host", "network"}:
                    # A 429 or auth error ends the run immediately.
                    break
                # A stale job/image may have been removed; do not retry.
                continue
            successful[group].append((time.monotonic() - start) * 1000)
            results["ok"] += 1

    # Publish is purposefully NOT measured: a live write-free probe cannot
    # estimate real jobs.add wall time. Simulator models it separately.
    return {
        "profile": aggregate(successful),
        "summary": {
            "total_read_requests": sum(results.values()),
            "max_planned_requests": 3 + 2 * samples,
            "successes": results["ok"],
            "errors": {key: value for key, value in sorted(results.items()) if key != "ok"},
            "live_writes": 0,
            "publish_latency": "unmeasured; explicitly modeled in simulator",
            "no_real_jobs_or_files_created": True,
            "min_interval_seconds": min_interval,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=3, choices=(1, 2, 3, 4))
    parser.add_argument("--min-interval", type=float, default=1.5)
    parser.add_argument("--output", type=Path,
                        default=Path("/data/metrics/read-only-profile.json"))
    args = parser.parse_args()
    if not args.output.is_absolute() or not args.output.is_relative_to("/data/metrics"):
        parser.error("Only an absolute output path under /data/metrics is supported.")
    try:
        result = calibrate(samples=args.samples, min_interval=args.min_interval)
    except (ValueError, NativeAPIError) as exc:
        # Avoid dumping full error details, tokens or URL.
        parser.exit(1, f"Read-only probe stopped ({type(exc).__name__}).\n")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(args.output.parent, 0o700)
    args.output.write_text(json.dumps(result["profile"], indent=2), encoding="utf-8")
    os.chmod(args.output, 0o600)
    print(json.dumps(result["summary"], indent=2))


if __name__ == "__main__":
    main()
