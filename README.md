# Annotatiathon — Event Operations & Supervisely Monitoring

A new, production-oriented application for the Técnico Bird Annotatiathon. Development happens **only on `main`**. The previous implementation is reference material, not the codebase for this rebuild.

## Current status

**Phase 1 started:** architecture and local backend foundation. The application is **not yet ready for event use**. No real login, roster management, job distribution, or annotation evaluation is implemented in this new codebase yet.

Read:
- [Product specification](docs/PRODUCT_SPEC.md)
- [Implementation roadmap and acceptance gates](docs/IMPLEMENTATION_ROADMAP.md)
- [Architecture and data ownership](docs/ARCHITECTURE.md)
- [Engineering rules](docs/ENGINEERING_RULES.md)

## Local development (Windows PowerShell)

Requires Python 3.12+, Docker with Compose, and Git.

```powershell
git switch main
git pull --ff-only origin main
Copy-Item .env.example .env
# Edit .env and choose a strong POSTGRES_PASSWORD and matching DATABASE_URL
docker compose -f compose.local.yaml --env-file .env up -d db
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
uvicorn annotatiathon.app:app --reload --host 127.0.0.1 --port 8000
```

Endpoints: `http://127.0.0.1:8000/health/live` and `/health/ready`.

Run tests: `python -m pytest -q`.

**Important:** This foundation currently exposes health endpoints only. It deliberately does not simulate event operations, create sample users, or auto-create a database schema. Staff authentication and database migrations are next milestones.

## Data safety

This repository is public. Do **not** commit actual registrant workbooks, CSV exports, private contact data, datasets, API keys, database dumps, or `.env`. Such material belongs in private local storage or the application database.
