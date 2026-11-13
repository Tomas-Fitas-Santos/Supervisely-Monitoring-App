"""Explain a Nightjar benchmark by measured stages, code paths and resources.

Consumes only local aggregate summary.json. No private Supervisely calls.
Usage: python scripts/report_full_workflow.py <benchmark-run-directory>
"""
import argparse
import json
from pathlib import Path


STAGES = (
    ("Submission detection", "submission_detection"),
    ("Validation queue wait", "validation_queue_wait"),
    ("Validation execution", "validation_duration"),
    ("Correction queue wait", "correction_queue_wait"),
    ("Correction confirmation", "correction_publish_duration"),
    ("New-job queue wait", "publish_queue_wait"),
    ("Initial job publishing", "primary_publish_duration"),
    ("Complete validation to next job", "complete_validation_to_new_job"),
)


def explain(summary: dict) -> dict:
    workload = summary.get("workload") or {}
    timing = summary.get("worker_timing") or {}
    resources = summary.get("resources") or {}
    stage_lags = timing.get("stage_lags") or {}
    detection = summary.get("submission_detection") or {}
    stages = []
    for title, name in STAGES:
        record = detection if name == "submission_detection" else stage_lags.get(name, {})
        if record and record.get("count", 0):
            stages.append({
                "stage": title, "metric": name, "count": record["count"],
                "p50_ms": record.get("p50_ms"), "p95_ms": record.get("p95_ms"),
                "p99_ms": record.get("p99_ms"),
            })
    hotspots = list((timing.get("code_hotspots") or [])[:12])
    sql = list((timing.get("sql_hotspots") or [])[:10])
    roles = []
    for name, value in resources.items():
        if name == "combined":
            continue
        roles.append({
            "role": name, "replicas": value.get("sampled_containers"),
            "peak_cpu_cores": value.get("max_aggregate_cpu_cores"),
            "peak_ram_mib": value.get("max_aggregate_working_set_mib"),
        })
    operations = (workload.get("database") or {})
    issues = list(operations.get("operation_errors") or [])
    lifecycle = workload.get("full_flow")
    full_required = workload.get("phase") == "full"
    complete = (bool(lifecycle) and
                lifecycle.get("source_frames") == 10800 and
                lifecycle.get("mirrored_frames") == 10800 and
                lifecycle.get("source_blocks_allocated") == 360 and
                lifecycle.get("jobs_with_native_bindings", 0) >= 360 and
                lifecycle.get("validated_frames", 0) >= 10800 and
                lifecycle.get("reviewed_frames", 0) >= 300 and
                (lifecycle.get("operation_kinds") or {}).get(
                    "restart_job:done", 0) >= 60)
    bottlenecks = sorted(
        [stage for stage in stages if isinstance(stage.get("p95_ms"), (int, float))],
        key=lambda stage: stage["p95_ms"], reverse=True,
    )
    return {
        "phase": workload.get("phase"),
        "fully_accepted": bool(complete) if full_required else None,
        "synthetic_api_latency": not workload.get("profile_mode", False),
        "source_to_launch_phases": workload.get("phases") or {},
        "lifecycle": lifecycle,
        "operation_errors": issues,
        "stage_timings": stages,
        "largest_observed_p95_stages": bottlenecks[:5],
        "code_hotspots_by_total_wall_time": hotspots,
        "sql_hotspots_by_total_wall_time": sql,
        "resources": {
            "combined_peak_cpu_cores": (resources.get("combined") or {}).get(
                "max_sample_cpu_cores"),
            "combined_peak_ram_mib": (resources.get("combined") or {}).get(
                "max_sample_working_set_mib"),
            "by_role": roles,
        },
        "note": (
            "Queue waits and processing spans can overlap; total wall milliseconds "
            "across concurrent workers are not exclusive CPU time. These are "
            "synthetic Supervisely operations, not live server timings."
        ),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    source = args.directory / "summary.json"
    report = explain(json.loads(source.read_text(encoding="utf-8-sig")))
    destination = args.directory / "pipeline-analysis.json"
    destination.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("FULL WORKFLOW DIAGNOSTICS")
    print("Phase:", report["phase"], "| accepted:", report["fully_accepted"])
    print("Source and plan:", report["source_to_launch_phases"])
    print("Lifecycle:", report["lifecycle"])
    print("Operation errors:", report["operation_errors"])
    print("Stage waits and execution p95 (seconds):")
    for stage in report["stage_timings"]:
        print("  %-40s %8.2f s (%d)" % (
            stage["stage"], (stage["p95_ms"] or 0) / 1000, stage["count"]))
    print("Top Python code paths (p95 ms; inclusive wall-time total ms):")
    for row in report["code_hotspots_by_total_wall_time"][:10]:
        print("  %-45s p95=%8.1f total=%10.1f" % (
            row["path"], row["p95_ms"], row["total_wall_ms"]))
    print("Top SQL statements (p95 ms; grouped by operation/table):")
    for row in report["sql_hotspots_by_total_wall_time"][:8]:
        print("  %-45s p95=%8.1f total=%10.1f" % (
            row["path"], row["p95_ms"], row["total_wall_ms"]))
    print("Peak combined CPU cores / memory MiB:",
          report["resources"]["combined_peak_cpu_cores"],
          report["resources"]["combined_peak_ram_mib"])
    for row in report["resources"]["by_role"]:
        print("  %-20s replicas=%s CPU=%s cores RAM=%s MiB" % (
            row["role"], row["replicas"], row["peak_cpu_cores"],
            row["peak_ram_mib"]))
    print("Saved:", destination)


if __name__ == "__main__":
    main()
