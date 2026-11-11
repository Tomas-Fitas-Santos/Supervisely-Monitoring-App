#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
project="${1:-nightjar-benchmark-30}"
output="${2:-.benchmark-data/query-profile}"
mkdir -p "$output"
compose=(docker compose -p "$project" -f compose.benchmark.yaml)
cleanup() { "${compose[@]}" stop db; }
trap cleanup EXIT
"${compose[@]}" up -d db
"${compose[@]}" run --rm --no-deps load python /bench/benchmark_full.py profile
"${compose[@]}" run --rm --no-deps load cat /data/results/query-profile.json > "$output/query-profile.json"
docker run --rm --entrypoint python nightjar-benchmark-runtime -c \
    "import importlib.metadata as m,json,sys; print(json.dumps({'python':sys.version, **{p:m.version(p) for p in ['supervisely','fastapi','sqlalchemy','psycopg']}},indent=2))" \
    > "$output/versions.json"
