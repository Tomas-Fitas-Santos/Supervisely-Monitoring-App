"""Regression checks for the adapted review+closeout branch integration.

These ensure the older closeout feature cannot accidentally replace modern
annotation review, 300-participant worker logic or the live Alembic chain.
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def test_one_linear_migration_head_after_merge():
    folder = ROOT / "migrations" / "versions"
    entries = {}
    for file in folder.glob("*.py"):
        text = file.read_text(encoding="utf-8")
        revision = re.search(r'^revision = "([^"]+)"', text, re.MULTILINE)
        predecessor = re.search(r'^down_revision = "([^"]+)"', text, re.MULTILINE)
        assert revision, f"Missing revision ID in {file}"
        assert revision.group(1) not in entries, f"Duplicate Alembic revision in {file}"
        entries[revision.group(1)] = predecessor.group(1) if predecessor else None
    assert entries["0028_native_job_numbers"] == "0027_mounted_source_folders"
    assert entries["0029_competition_closeout"] == "0028_native_job_numbers"
    parents = {rev for rev in entries.values() if rev is not None}
    heads = set(entries) - parents
    assert heads == {"0029_competition_closeout"}


def test_review_ui_keeps_missing_landmark_tracking_and_box_reasons():
    ui = (ROOT / "web" / "src" / "shared_review.tsx").read_text(encoding="utf-8")
    api = (ROOT / "src" / "annotatiathon" / "review_queue_api.py").read_text(
        encoding="utf-8",
    )
    assert 'missing_classes:missingClasses' in ui
    assert 'className="shared-review-missing"' in ui
    assert "boxReasons" in ui
    assert "RefreshButton" in ui
    assert "Choose placement mistake or sabotage" in api
    assert "allow_finalizing=True" in api


def test_worker_retains_numbering_closeout_and_benchmark_contract():
    code = (ROOT / "src" / "annotatiathon" / "native_worker.py").read_text(
        encoding="utf-8",
    )
    assert "ensure_native_job_number" in code
    assert "numbered_job_name" in code
    assert "require_open_event" in code
    assert "finish_pending_closeouts" in code
    assert "BENCHMARK_ANNOTATORS" in code
