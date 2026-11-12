"""Summarize private Nightjar real-worker JSONL timing data.

Writes aggregate per-category latencies for the isolated Supervisely simulator.
Never copies job identifiers, tokens, request URLs, headers or annotation JSON
into the simulation profile. All times are wall-clock milliseconds.
"""
import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
import statistics


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * fraction) - 1))
    return round(ordered[rank], 2)


def summarize(records: list[dict]) -> tuple[dict, dict]:
    groups: dict[str, list[float]] = defaultdict(list)
    endpoints: dict[str, list[float]] = defaultdict(list)
    outcomes: dict[str, int] = defaultdict(int)
    stages: dict[str, dict[str, int]] = defaultdict(dict)
    next_job_relations: dict[str, str] = {}
    for row in records:
        if row.get("kind") == "http":
            duration = row.get("duration_ms")
            if not isinstance(duration, (int, float)) or not math.isfinite(duration):
                continue
            group = row.get("group")
            endpoint = row.get("endpoint")
            if not isinstance(group, str) or not isinstance(endpoint, str):
                continue
            groups[group].append(float(duration))
            endpoints[endpoint].append(float(duration))
            outcomes[str(row.get("outcome", "unknown"))] += 1
        elif row.get("kind") == "stage":
            job = row.get("job_id")
            stamp = row.get("unix_ms")
            stage = row.get("stage")
            if (isinstance(job, str) and isinstance(stamp, int)
                    and isinstance(stage, str)):
                stages[job][stage] = stamp
                if stage == "next_job_queued" and isinstance(row.get("operation_id"), str):
                    next_job_relations[job] = row["operation_id"]

    def stat(values: list[float]) -> dict:
        return {"count": len(values), "p50_ms": percentile(values, .5),
                "p95_ms": percentile(values, .95),
                "p99_ms": percentile(values, .99),
                "mean_ms": round(statistics.mean(values), 2)}

    spans: dict[str, list[float]] = defaultdict(list)
    for job in stages.values():
        for key, first, last in [
            ("detected_to_validation", "submission_detected", "validation_started"),
            ("detected_to_validation_queue", "submission_detected", "validation_queued"),
            ("validation_queue_wait", "validation_queued", "validation_started"),
            ("detected_to_validation_complete", "submission_detected", "validation_completed"),
            ("correction_full_turnaround", "submission_detected", "correction_publish_confirmed"),
            ("correction_queue_wait", "correction_queued", "correction_publish_started"),
            ("correction_queued_to_confirmed", "correction_queued", "correction_publish_confirmed"),
            ("validation_duration", "validation_started", "validation_completed"),
            ("publish_queue_wait", "publish_queued", "publish_started"),
            ("detected_to_correction_queued", "submission_detected", "correction_queued"),
            ("correction_queue_to_publish", "correction_queued", "correction_publish_started"),
            ("correction_publish_duration", "correction_publish_started", "correction_publish_confirmed"),
            ("primary_publish_duration", "publish_started", "publish_confirmed"),
        ]:
            if first in job and last in job and job[last] >= job[first]:
                spans[key].append(float(job[last] - job[first]))

    # Link the original completed job to the NEW job UUID. This measures
    # participant wait time after validation, not merely the publish RPC.
    for original, next_job_id in next_job_relations.items():
        previous = stages.get(original, {})
        successor = stages.get(next_job_id, {})
        confirmed = successor.get("publish_confirmed")
        if confirmed is None:
            continue
        for metric, start_stage in (
            ("complete_validation_to_new_job", "validation_completed"),
            ("submission_detected_to_new_job", "submission_detected"),
        ):
            begin = previous.get(start_stage)
            if begin is not None and confirmed >= begin:
                spans[metric].append(float(confirmed - begin))

    profile = {group: stat(values) for group, values in sorted(groups.items())}
    report = {
        "http_requests": sum(outcomes.values()),
        "outcomes": dict(sorted(outcomes.items())),
        "groups": profile,
        "endpoints": {endpoint: stat(values)
                      for endpoint, values in sorted(endpoints.items())},
        "stage_lags": {key: stat(values) for key, values in sorted(spans.items())},
        "jobs_with_stages": len(stages),
        "next_job_relations": len(next_job_relations),
        "next_jobs_confirmed": sum(
            stages.get(target, {}).get("publish_confirmed") is not None
            for target in next_job_relations.values()
        ),
    }
    return report, profile


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Worker JSONL copied privately from /data/metrics")
    parser.add_argument("--report", type=Path, default=Path("worker-timing-report.json"))
    parser.add_argument("--profile", type=Path, default=Path("scripts/benchmark_profiles/measured.json"))
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.input.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    report, profile = summarize(rows)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.profile.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    # Aggregate-only simulator input; no job ids or personal details.
    args.profile.write_text(json.dumps(profile, indent=2), encoding="utf-8")
    print(json.dumps({"http_requests": report["http_requests"],
                      "stage_lags": report["stage_lags"],
                      "profile_file": str(args.profile),
                      "report_file": str(args.report)}, indent=2))


if __name__ == "__main__":
    main()
