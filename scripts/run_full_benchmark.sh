#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
action="${1:-run}"
project="${5:-${BENCHMARK_PROJECT:-nightjar-benchmark}}"
compose=(docker compose -p "$project" -f compose.benchmark.yaml)

if [[ "$action" == setup ]]; then
    for image in node:22-alpine nginx:1.27-alpine postgres:17-alpine; do
        for attempt in 1 2 3 4 5; do
            if docker pull "$image"; then break; fi
            if [[ "$attempt" == 5 ]]; then exit 1; fi
        done
    done
    "${compose[@]}" build web
    exit
fi

if [[ "$action" == stop ]]; then
    "${compose[@]}" stop
    exit
fi

if [[ "$action" == cancel ]]; then
    identifiers="$(docker ps -q --filter "label=com.docker.compose.project=$project" \
        --filter 'label=com.docker.compose.service=load')"
    if [[ -n "$identifiers" ]]; then docker stop $identifiers; fi
    exit
fi

if [[ "$action" == up ]]; then
    "${compose[@]}" up -d web worker
    exit
fi

duration="${2:-600}"
latency="${3:-0.05}"
failure_rate="${4:-0}"
output=".benchmark-data/full-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$output"
"${compose[@]}" up -d web worker
"${compose[@]}" config > "$output/compose.yaml"
docker info --format '{{json .}}' > "$output/docker-info.json"
containers=()
for service in api worker db web; do
    containers+=("$("${compose[@]}" ps -q "$service")")
done
load_name="$project-load-$(date +%s)"
mode=load
if [[ "$action" == history ]]; then mode=history; fi
"${compose[@]}" run -d --name "$load_name" load python /bench/benchmark_full.py "$mode" \
    --seconds "$duration" --latency "$latency" --fail-rate "$failure_rate"
echo "Benchmark output $output"
while [[ "$(docker inspect -f '{{.State.Running}}' "$load_name")" == true ]]; do
    docker stats --no-stream --format '{{json .}}' "${containers[@]}" >> "$output/resources.jsonl"
    sleep 4
done
docker logs "$load_name" > "$output/load.log" 2>&1
for service in api worker db web; do
    identifier="$("${compose[@]}" ps -q "$service")"
    docker exec "$identifier" sh -c 'cat /sys/fs/cgroup/memory.peak; cat /sys/fs/cgroup/cpu.stat' \
        > "$output/$service-cgroup.txt"
done
docker inspect "$load_name" "${containers[@]}" > "$output/container-state.json"
docker cp "$load_name:/data/results/." "$output/" || true
"${compose[@]}" logs --no-color > "$output/services.log" 2>&1
code="$(docker inspect -f '{{.State.ExitCode}}' "$load_name")"
"${compose[@]}" stop
echo "Benchmark finished with exit code $code, results in $output"
exit "$code"
