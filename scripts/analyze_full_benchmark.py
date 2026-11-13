"""Summarize Docker samples and completed lifecycle results for one benchmark."""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import re
import statistics
from math import ceil


def mib(value):
    match = re.fullmatch(r"([0-9.]+)([A-Za-z]+)", value.strip())
    factors = {"B": 1 / 1024**2, "kB": 1000 / 1024**2, "KiB": 1 / 1024,
               "MB": 1000**2 / 1024**2, "MiB": 1, "GB": 1000**3 / 1024**2, "GiB": 1024}
    return float(match[1]) * factors[match[2]]


SERVICE_PATTERN = re.compile(
    r"-(status-worker|validation-worker|correction-worker|recovery-worker|"
    r"bulk-worker|supervisely-sim|worker|api|db|web|load|seed)-[0-9]+$"
)


def service_name(container: str) -> str:
    match = SERVICE_PATTERN.search(container)
    return match.group(1) if match else "other"


def summarize(directory):
    rows = [json.loads(line) for line in (directory / "resources.jsonl").read_text(encoding="utf-8-sig").splitlines()
            if line.strip()]
    services = defaultdict(list)
    by_service_sample = defaultdict(list)
    for row in rows:
        service = service_name(row["Name"])
        values = {"cores": float(row["CPUPerc"].rstrip("%")) / 100,
                  "working_set_mib": mib(row["MemUsage"].split("/")[0])}
        services[service].append(values)
        by_service_sample[(service, row.get("sample_ms", 0))].append(values)
    summary = {}
    for service, samples in services.items():
        summary[service] = {
            "average_cpu_cores": statistics.mean(item["cores"] for item in samples),
            "max_sample_cpu_cores": max(item["cores"] for item in samples),
            "max_sample_working_set_mib": max(item["working_set_mib"] for item in samples),
        }
        summary[service]["sampled_containers"] = len({
            row["Name"] for row in rows if service_name(row["Name"]) == service
        })
        batches = [group for (name, _stamp), group in by_service_sample.items()
                   if name == service]
        summary[service]["max_aggregate_cpu_cores"] = max((
            sum(item["cores"] for item in group) for group in batches
        ), default=0)
        summary[service]["max_aggregate_working_set_mib"] = max((
            sum(item["working_set_mib"] for item in group) for group in batches
        ), default=0)
        peak_file = directory / f"{service}-cgroup.txt"
        if peak_file.exists():
            lines = peak_file.read_text().splitlines()
            summary[service]["peak_cgroup_memory_mib"] = int(lines[0]) / 1024**2
            summary[service]["cpu_cgroup"] = {
                line.split()[0]: int(line.split()[1]) for line in lines[1:]
            }
    # Samples contain independently scaled publishers and validators.
    # Aggregate CPU/RAM by timestamp, not by a fixed container count.
    by_sample = defaultdict(list)
    for index, row in enumerate(rows):
        by_sample[row.get("sample_ms", index // max(1, len(services)))].append(row)
    groups = list(by_sample.values())
    summary["combined"] = {
        "max_sample_cpu_cores": max((sum(float(item["CPUPerc"].rstrip("%")) / 100
                                      for item in group) for group in groups), default=0),
        "max_sample_working_set_mib": max((sum(mib(item["MemUsage"].split("/")[0])
                                             for item in group) for group in groups), default=0),
    }
    result = {"resources": summary}
    if (directory / "result.json").exists():
        result["workload"] = json.loads((directory / "result.json").read_text())
    if (directory / "requests.json").exists():
        requests = json.loads((directory / "requests.json").read_text())
        result["http_errors"] = [item for item in requests
                                 if not isinstance(item["status"], int) or item["status"] >= 400]
        paths = defaultdict(list)
        for item in requests:
            paths[item["method"] + " " + item["path"]].append(item["ms"])
        result["slowest_endpoint_p95"] = sorted([
            {"endpoint": path, "count": len(times), "p95_ms": sorted(times)[int(len(times) * .95)]}
            for path, times in paths.items()
        ], key=lambda row: row["p95_ms"], reverse=True)[:15]
    worker_trace = directory / "worker-timing.jsonl"
    if worker_trace.exists() and worker_trace.stat().st_size:
        from analyze_worker_timing import summarize as summarize_worker
        worker_rows = [json.loads(line) for line in worker_trace.read_text(encoding="utf-8").splitlines()
                       if line.strip()]
        worker_summary, _ = summarize_worker(worker_rows)
        result["worker_timing"] = worker_summary
        started = result.get("workload", {}).get("simulator", {}).get("started_unix_ms")
        if isinstance(started, int):
            received = sorted(row["unix_ms"] for row in worker_rows
                              if row.get("kind") == "stage"
                              and row.get("stage") == "publish_confirmed"
                              and isinstance(row.get("unix_ms"), int))
            workload = result.get("workload", {})
            phase = workload.get("phase", "legacy")
            records = [row for row in worker_rows if row.get("kind") == "stage"]
            original_pattern = re.compile(r"job-(?:[0-9]|[1-9][0-9]|[12][0-9]{2})$")
            starts = [
                row["unix_ms"] for row in records
                if row.get("stage") == "publish_queued"
                and original_pattern.fullmatch(str(row.get("job_id", "")))
            ]
            original_confirms = sorted(
                row["unix_ms"] for row in records
                if row.get("stage") == "publish_confirmed"
                and original_pattern.fullmatch(str(row.get("job_id", "")))
            )
            origin = min(starts) if starts else started
            cohort = {
                "phase": phase,
                "cohort_target": 300,
                "cohort_origin_ms": origin,
                "confirmed_count": len(original_confirms) if phase == "initial" else len(received),
                "first_confirmed_ms": original_confirms[0] - origin
                    if phase == "initial" and original_confirms else (
                        received[0] - started if received else None),
                "one_hundred_fiftieth_confirmed_ms": (
                    original_confirms[149] - origin if len(original_confirms) >= 150 else None
                ) if phase == "initial" else (
                    received[149] - started if phase == "full"
                    and len(received) >= 150 else None
                ),
                "two_hundred_ninetieth_confirmed_ms": (
                    original_confirms[289] - origin if len(original_confirms) >= 290 else None
                ) if phase == "initial" else (
                    received[289] - started if len(received) >= 290 else None),
                "three_hundredth_confirmed_ms": (
                    original_confirms[299] - origin if len(original_confirms) >= 300 else None
                ) if phase == "initial" else (
                    received[299] - started if phase == "full"
                    and len(received) >= 300 else None
                ),
                "last_observed_confirmed_ms": (
                    original_confirms[-1] - origin if original_confirms else None
                ) if phase == "initial" else (received[-1] - started if received else None),
            }
            if phase in {"initial", "full"}:
                count = len(original_confirms) if phase == "initial" else len(received)
                cohort["remaining_unpublished"] = max(0, 300 - count)
                cohort["confirmed_per_minute_observed"] = round(
                    60000 * count /
                    max(1, cohort["last_observed_confirmed_ms"] or 1), 2
                )
            result["publication_cohort"] = cohort
            if phase in {"turnaround", "full"}:
                # Compare actual worker detection milestones against the
                # simulator's burst/stagger submission clock.
                detection = []
                for row in records:
                    if row.get("stage") != "submission_detected":
                        continue
                    match = original_pattern.fullmatch(str(row.get("job_id", "")))
                    if not match:
                        continue
                    person = int(match.group().split("-")[-1])
                    extra_ms = 0
                    if not workload.get("simulator", {}).get("burst", False):
                        extra_ms = person * workload.get("frames_per_job", 30) * workload.get(
                            "seconds_per_frame", 90) * 1000 / max(
                                1, workload.get("annotators", 300))
                    delay = row["unix_ms"] - started - extra_ms
                    if delay >= 0:
                        detection.append(delay)
                if detection:
                    observed = sorted(detection)
                    result["submission_detection"] = {
                        "count": len(observed),
                        "p50_ms": observed[ceil(len(observed) * .50) - 1],
                        "p95_ms": observed[ceil(len(observed) * .95) - 1],
                        "p99_ms": observed[ceil(len(observed) * .99) - 1],
                        "max_ms": observed[-1],
                    }
    (directory / "summary.json").write_text(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    arguments = parser.parse_args()
    print(json.dumps(summarize(arguments.directory), indent=2))
