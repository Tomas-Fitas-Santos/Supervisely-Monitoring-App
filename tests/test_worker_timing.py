"""The live probe records durations but never credentials or HTTP payloads."""
import importlib.util
from pathlib import Path

from annotatiathon.worker_timing import _safe_endpoint, endpoint_group


def test_worker_endpoint_normalization_drops_hosts_tokens_and_query():
    assert _safe_endpoint(
        "https://app.supervisely.com/public/api/v3/jobs.info?"
        "apiKey=secret&user=private"
    ) == "jobs.info"
    assert _safe_endpoint("https://app.supervisely.com/api/auth?token=secret") is None
    assert _safe_endpoint("http://example.org/public/api/v3/unsafe/path") is None
    assert endpoint_group("jobs.info") == "status"
    assert endpoint_group("jobs.add") == "publish"
    assert endpoint_group("annotations.info") == "annotation"
    assert endpoint_group("datasets.list") == "lookup"


def test_timing_report_and_profile_exclude_identifiers_and_credentials():
    source = Path(__file__).resolve().parents[1] / "scripts" / "analyze_worker_timing.py"
    spec = importlib.util.spec_from_file_location("worker_bench_analyzer", source)
    assert spec is not None and spec.loader is not None
    analyzer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(analyzer)
    rows = [
        {"kind": "http", "group": "status", "endpoint": "jobs.info",
         "duration_ms": 100, "outcome": "ok", "status": 200},
        {"kind": "http", "group": "status", "endpoint": "jobs.info",
         "duration_ms": 500, "outcome": "http_error", "status": 503},
        {"kind": "http", "group": "annotation", "endpoint": "annotations.info",
         "duration_ms": 200, "outcome": "ok", "status": 200},
        {"kind": "stage", "job_id": "job-private", "stage": "submission_detected",
         "unix_ms": 1000},
        {"kind": "stage", "job_id": "job-private", "stage": "correction_queued",
         "unix_ms": 1120},
        {"kind": "stage", "job_id": "job-private", "stage": "correction_publish_started",
         "unix_ms": 1220},
        {"kind": "stage", "job_id": "job-private", "stage": "correction_publish_confirmed",
         "unix_ms": 1620},
    ]
    report, profile = analyzer.summarize(rows)
    assert profile["status"]["count"] == 2
    assert profile["status"]["p95_ms"] == 500
    assert profile["annotation"]["p50_ms"] == 200
    assert report["stage_lags"]["correction_queue_to_publish"]["p50_ms"] == 100
    assert report["stage_lags"]["correction_publish_duration"]["p50_ms"] == 400
    assert report["outcomes"]["http_error"] == 1
    assert "job-private" not in str(report)
    assert "job-private" not in str(profile)
