#!/bin/sh
set -eu

# PostgreSQL stays on a private Docker network. Quote credential segments
# so URL-special characters in a generated database password work safely.
export DATABASE_URL="$(python -c 'import os; from urllib.parse import quote; print("postgresql+psycopg://{}:{}@db:5432/{}".format(*(quote(os.environ[k], safe="") for k in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"))))')"

exec "$@"
