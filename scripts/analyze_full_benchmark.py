"""Summarize Docker samples and completed lifecycle results for one benchmark."""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import re
import statistics


def mib(value):
    match = re.fullmatch(r"([0-9.]+)([A-Za-z]+)", value.strip())
    factors = {"B": 1 / 1024**2, "kB": 1000 / 1024**2, "KiB": 1 / 1024,
               "MB": 1000**2 / 1024**2, "MiB": 1, "GB": 1000**3 / 1024**2, "GiB": 1024}
    return float(match[1]) * factors[match[2]]


def summarize(directory):
    rows = [json.loads(line) for line in (directory / "resources.jsonl").read_text().splitlines()]
    services = defaultdict(list)
    for row in rows:
        service = row["Name"].rsplit("-", 2)[-2]
        services[service].append({"cores": float(row["CPUPerc"].rstrip("%")) / 100,
                                  "working_set_mib": mib(row["MemUsage"].split("/")[0])})
    summary = {}
    for service, samples in services.items():
        summary[service] = {
            "average_cpu_cores": statistics.mean(item["cores"] for item in samples),
            "max_sample_cpu_cores": max(item["cores"] for item in samples),
            "max_sample_working_set_mib": max(item["working_set_mib"] for item in samples),
        }
        peak_file = directory / f"{service}-cgroup.txt"
        if peak_file.exists():
            lines = peak_file.read_text().splitlines()
            summary[service]["peak_cgroup_memory_mib"] = int(lines[0]) / 1024**2
            summary[service]["cpu_cgroup"] = {
                line.split()[0]: int(line.split()[1]) for line in lines[1:]
            }
    groups = [rows[index:index + 4] for index in range(0, len(rows), 4)]
    summary["combined"] = {
        "max_sample_cpu_cores": max(sum(float(row["CPUPerc"].rstrip("%")) / 100 for row in group)
                                    for group in groups),
        "average_cpu_cores": sum(service["average_cpu_cores"] for service in summary.values()),
        "max_sample_working_set_mib": max(sum(mib(row["MemUsage"].split("/")[0]) for row in group)
                                           for group in groups),
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
    (directory / "summary.json").write_text(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    arguments = parser.parse_args()
    print(json.dumps(summarize(arguments.directory), indent=2))
