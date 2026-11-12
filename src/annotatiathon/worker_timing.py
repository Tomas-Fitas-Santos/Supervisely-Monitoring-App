"""Opt-in, privacy-safe timing records for REAL Supervisely SDK worker calls.

No payloads, query strings, hosts, user names, auth headers or tokens are
recorded. This module instruments requests.Session.send and httpx.Client.send,
which cover SDK HTTP requests and the application's own sync REST adapter.

Use NIGHTJAR_WORKER_TIMING_FILE=/data/metrics/supervisely-timing.jsonl in
the existing SINGLE live worker; do not run extra live publishers for timing.
"""
import json
import os
from pathlib import Path
import re
from threading import Lock
import time
from urllib.parse import urlsplit

_lock = Lock()
_output = None
_installed = False
_API_PATH = re.compile(r"^/public/api/v3/([A-Za-z0-9_.-]{1,72})$")


def endpoint_group(endpoint: str) -> str:
    if endpoint in {"jobs.info", "jobs.list"}:
        return "status"
    if endpoint.startswith("annotations."):
        return "annotation"
    if endpoint in {"jobs.add", "jobs.create"}:
        return "publish"
    if endpoint in {
        "datasets.info", "datasets.list", "images.info", "images.list",
        "projects.info", "projects.list", "projects.meta", "workspaces.info",
        "members.list", "users.info",
    }:
        return "lookup"
    return "other"


def _safe_endpoint(url: str) -> str | None:
    try:
        match = _API_PATH.fullmatch(urlsplit(url).path)
    except (TypeError, ValueError):
        return None
    return match.group(1) if match else None


def _write(record: dict) -> None:
    if _output is None:
        return
    with _lock:
        # The pre-opened file has mode 0600 on Linux. Never write HTTP bodies.
        _output.write(json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n")
        _output.flush()


def milestone(stage: str, *, job_id: str | None = None,
              operation_id: str | None = None, frame_count: int | None = None) -> None:
    """Record submission/queue/publication milestones without personal fields."""
    if _output is None:
        return
    record = {"kind": "stage", "unix_ms": round(time.time() * 1000), "stage": stage}
    if job_id:
        record["job_id"] = job_id
    if operation_id:
        record["operation_id"] = operation_id
    if frame_count is not None:
        record["frame_count"] = frame_count
    _write(record)


def enable(path: str | os.PathLike[str]) -> None:
    """Instrument HTTP attempts only while explicitly enabled for this worker."""
    global _installed, _output
    if _installed:
        return
    import httpx
    import requests

    location = Path(path)
    if not location.is_absolute():
        raise ValueError("Worker timing file must be an absolute path.")
    location.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(location), os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
    _output = os.fdopen(fd, "a", encoding="utf-8", buffering=1)

    original_requests_send = requests.sessions.Session.send
    original_httpx_send = httpx.Client.send

    def request_send(session, request, **kwargs):
        endpoint = _safe_endpoint(request.url)
        if endpoint is None:
            return original_requests_send(session, request, **kwargs)
        start = time.perf_counter()
        status = None
        outcome = "error"
        try:
            response = original_requests_send(session, request, **kwargs)
            status = response.status_code
            outcome = "ok" if status < 400 else "http_error"
            return response
        except requests.exceptions.Timeout:
            outcome = "timeout"
            raise
        except requests.exceptions.RequestException:
            outcome = "network_error"
            raise
        finally:
            _write({
                "kind": "http", "unix_ms": round(time.time() * 1000),
                "endpoint": endpoint, "group": endpoint_group(endpoint),
                "method": request.method, "status": status, "outcome": outcome,
                "duration_ms": round((time.perf_counter() - start) * 1000, 3),
            })

    def httpx_send(session, request, **kwargs):
        endpoint = _safe_endpoint(str(request.url))
        if endpoint is None:
            return original_httpx_send(session, request, **kwargs)
        start = time.perf_counter()
        status = None
        outcome = "error"
        try:
            response = original_httpx_send(session, request, **kwargs)
            status = response.status_code
            outcome = "ok" if status < 400 else "http_error"
            return response
        except httpx.TimeoutException:
            outcome = "timeout"
            raise
        except httpx.RequestError:
            outcome = "network_error"
            raise
        finally:
            _write({
                "kind": "http", "unix_ms": round(time.time() * 1000),
                "endpoint": endpoint, "group": endpoint_group(endpoint),
                "method": request.method, "status": status, "outcome": outcome,
                "duration_ms": round((time.perf_counter() - start) * 1000, 3),
            })

    requests.sessions.Session.send = request_send
    httpx.Client.send = httpx_send
    _installed = True
    milestone("timing_enabled")
