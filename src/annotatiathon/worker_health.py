"""Best-effort process telemetry. Never controls claims, leases or recovery."""
import json
import logging
import os
import time
from pathlib import Path
from uuid import uuid4

from .config import get_settings

_instance = uuid4().hex
_cycles = {}
_last_warning = 0.0


def health_directory():
    return Path(get_settings().local_data_dir) / "operations-health"


def report_cycle(role, state):
    # Files are shared by the existing media mount; no extra database writes.
    # Separate start/finish timestamps expose a live but stuck SDK call.
    try:
        now = int(time.time())
        previous = _cycles.get(role, {})
        row = {"role": role, "instance": _instance, "state": state, "updated_at": now,
               "started_at": now if state == "working" else previous.get("started_at"),
               "finished_at": previous.get("finished_at") if state == "working" else now}
        _cycles[role] = row
        directory = health_directory() / "workers"
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{_instance}-{role}.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(row), encoding="utf-8")
        os.replace(temporary, target)
    except Exception:  # noqa: BLE001 - telemetry must never stop event processing
        # A full/unavailable telemetry volume must not break participant jobs.
        global _last_warning
        if time.monotonic() - _last_warning > 60:
            logging.getLogger(__name__).warning("Worker telemetry unavailable; check the shared media volume.")
            _last_warning = time.monotonic()


def worker_status(now, expected):
    rows = []
    unreadable = 0
    try:
        paths = list((health_directory() / "workers").glob("*.json"))
        # Old container records do not count as missing if replacements report.
        for path in paths:
            try:
                if path.stat().st_size > 4096:
                    raise ValueError("oversized telemetry")
                row = json.loads(path.read_text(encoding="utf-8"))
                if row["role"] not in expected or row["state"] not in {"working", "idle", "error", "paused"}:
                    continue
                updated = int(row["updated_at"])
                if updated > now + 60:
                    raise ValueError("future telemetry")
                row["age_seconds"] = max(0, now - updated)
                rows.append(row)
            except (OSError, ValueError, KeyError, TypeError):
                unreadable += 1
    except OSError:
        unreadable += 1
    result = []
    for role, count in expected.items():
        candidates = sorted((r for r in rows if r["role"] == role), key=lambda r: r["updated_at"], reverse=True)
        selected = candidates[:count]
        fresh = [r for r in selected if r["age_seconds"] <= 90]
        errors = sum(r["state"] == "error" for r in fresh)
        overdue = [r for r in selected if r["age_seconds"] > 90]
        result.append({"role": role, "expected": count, "reporting": len(fresh),
                       "errors": errors, "state": "warning" if len(fresh) < count or errors else "ok",
                       "last_seen": selected[0]["updated_at"] if selected else None,
                       "longest_unfinished_seconds": max((r["age_seconds"] for r in overdue
                                                           if r["state"] == "working"), default=0)})
    return result, unreadable


def finish_cycle(role):
    report_cycle(role, "error" if _cycles.get(role, {}).get("state") == "error" else "idle")
