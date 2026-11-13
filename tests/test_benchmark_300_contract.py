"""The isolated capacity model matches the official 300-person event sizing."""
import importlib.util
from pathlib import Path


def test_300_person_simulator_defaults_and_valid_frame_shape():
    script = Path(__file__).resolve().parents[1] / "scripts" / "benchmark_full.py"
    spec = importlib.util.spec_from_file_location("nightjar_benchmark_full", script)
    assert spec is not None and spec.loader is not None
    bench = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bench)
    assert bench.ANNOTATORS == 300
    assert bench.MONITORS == 12
    assert bench.FRAMES == 30
    assert bench.SECONDS_PER_FRAME == 90

    annotated = bench.annotation(140001)
    objects = annotated["objects"]
    class_counts = {title: sum(obj["classTitle"] == title for obj in objects)
                    for title in ("bird", "left_eye", "right_eye", "beak", "crown")}
    assert class_counts == dict.fromkeys(class_counts, 1)
    assert len(objects) == 5
    assert sum(item["geometryType"] == "rectangle" for item in objects) == 1
    assert sum(item["geometryType"] == "point" for item in objects) == 4


def test_worker_resource_analyzer_distinguishes_status_validation_and_publishers():
    script = Path(__file__).resolve().parents[1] / "scripts" / "analyze_full_benchmark.py"
    spec = importlib.util.spec_from_file_location("nightjar_benchmark_analyzer", script)
    assert spec is not None and spec.loader is not None
    analyzer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(analyzer)
    names = {
        "nightjar-bench-300-test-p2-v2-status-worker-1": "status-worker",
        "nightjar-bench-300-test-p2-v2-validation-worker-2": "validation-worker",
        "nightjar-bench-300-test-p2-v2-worker-1": "worker",
        "nightjar-bench-300-test-p2-v2-recovery-worker-1": "recovery-worker",
        "nightjar-bench-300-test-p2-v2-bulk-worker-1": "bulk-worker",
        "nightjar-bench-300-test-p2-v2-api-1": "api",
        "nightjar-bench-300-test-p2-v2-db-1": "db",
    }
    for container, expected in names.items():
        assert analyzer.service_name(container) == expected


def test_worker_sweep_caches_builds_without_reusing_test_databases():
    """One build per source change; each 300-person run uses isolated volumes."""
    script = (Path(__file__).resolve().parents[1] / "scripts" /
              "run_worker_sweep.ps1").read_text(encoding="utf-8")
    assert '[switch]$Rebuild' in script
    assert "Get-BenchmarkBuildSignature" in script
    assert "worker-sweep-build.sha256" in script
    assert "docker image inspect" in script
    assert "build api worker web" in script
    assert "up -d --no-build --scale" in script
    # docker compose run with load's depends_on graph had silently removed all
    # scaled replica-2/3 containers during the actual 300-job tests.
    assert "run -d --no-deps --name $loadName" in script
    assert "Benchmark load altered worker scaling" in script
    assert "Benchmark worker count changed during load" in script
    assert "up -d --build" not in script
    assert '"nightjar-bench-300-$stamp-$Phase-p$publishers-v$validatorsRun-c$correctorsRun-s$statusRun"' in script
    assert "down --volumes --remove-orphans" in script


def test_capacity_sweep_separates_initial_release_from_complete_turnaround():
    root = Path(__file__).resolve().parents[1]
    runner = (root / "scripts" / "run_worker_sweep.ps1").read_text(encoding="utf-8")
    compose = (root / "compose.benchmark.yaml").read_text(encoding="utf-8")
    fixture = (root / "scripts" / "benchmark_full.py").read_text(encoding="utf-8")
    for param in ('[string]$Phase = "turnaround"', '[int]$Correctors = 1',
                  '[int]$StatusWorkers = 1', '[double]$IncompleteRate = 0.20'):
        assert param in runner
    for role in ("worker", "validation-worker", "correction-worker", "status-worker"):
        assert f'Name = "{role}"' in runner
    assert "label=com.docker.compose.service=" in runner
    assert "BENCHMARK_PHASE:" in compose
    assert "BENCHMARK_INCOMPLETE_RATE:" in compose
    assert 'PHASE == "initial"' in fixture
    assert 'PHASE == "turnaround"' in fixture
    assert '"shared_primary"' in fixture
    assert '"restart_job:done"' not in fixture  # job state comes from SQL, not fake stats
    assert '"/api/v1/review/jobs/{job_id}/decision"' not in fixture


def test_automated_turnaround_metrics_link_original_and_next_native_job():
    script = Path(__file__).resolve().parents[1] / "scripts" / "analyze_worker_timing.py"
    spec = importlib.util.spec_from_file_location("nightjar_worker_metrics", script)
    analyzer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(analyzer)

    rows = [
        {"kind": "stage", "job_id": "original", "stage": "submission_detected", "unix_ms": 1000},
        {"kind": "stage", "job_id": "original", "stage": "validation_queued", "unix_ms": 1200},
        {"kind": "stage", "job_id": "original", "stage": "validation_started", "unix_ms": 1600},
        {"kind": "stage", "job_id": "original", "stage": "validation_completed", "unix_ms": 1700},
        {"kind": "stage", "job_id": "original", "stage": "next_job_queued",
         "operation_id": "replacement", "unix_ms": 1710},
        {"kind": "stage", "job_id": "replacement", "stage": "publish_queued", "unix_ms": 1710},
        {"kind": "stage", "job_id": "replacement", "stage": "publish_started", "unix_ms": 2000},
        {"kind": "stage", "job_id": "replacement", "stage": "publish_confirmed", "unix_ms": 2500},
    ]
    report, _profile = analyzer.summarize(rows)
    assert report["next_job_relations"] == 1
    assert report["next_jobs_confirmed"] == 1
    assert report["stage_lags"]["validation_queue_wait"]["p95_ms"] == 400
    assert report["stage_lags"]["complete_validation_to_new_job"]["p95_ms"] == 800
    assert report["stage_lags"]["submission_detected_to_new_job"]["p95_ms"] == 1500


def test_worker_sweep_scales_requested_role_and_verifies_all_replicas():
    """All roles support 8+ replicas with verification and resource warnings."""
    root = Path(__file__).resolve().parents[1]
    script = (root / "scripts" / "run_worker_sweep.ps1").read_text(encoding="utf-8")
    assert '[ValidateSet("publisher", "validation", "correction", "status")]' in script
    assert '[ValidateRange(1, 16)][int[]]$Workers' in script
    for flag in ("PublisherBaseline", "Validators", "Correctors", "StatusWorkers"):
        assert f"[ValidateRange(1, 16)][int]${flag}" in script
    assert "[int]$PublisherBaseline = 2" in script
    assert "docker info --format '{{.NCPU}}'" in script
    assert "docker info --format '{{.MemTotal}}'" in script
    assert "combined CPU limits" in script
    assert "Combined container memory limits" in script
    for role, actual, service in [
        ("publisher", "publishers", "worker"),
        ("validation", "validatorsRun", "validation-worker"),
        ("correction", "correctorsRun", "correction-worker"),
        ("status", "statusRun", "status-worker"),
    ]:
        assert f'$SweepRole -eq "{role}"' in script
        assert f'--scale "{service}=${actual}"'.replace("\\$", "$") in script
        assert f'Name = "{service}"; Expected = ${actual}'.replace("\\$", "$") in script
    assert '"--no-deps"' not in script  # Use real Compose flags, not a literal array
    assert "run -d --no-deps --name $loadName" in script
    assert "Benchmark load altered worker scaling" in script
    assert "Benchmark worker count changed during load" in script
    assert "down --volumes --remove-orphans" in script
    for name in ("SweepRole", "SweepCount", "Publishers", "Validators",
                 "Correctors", "StatusWorkers"):
        assert f"{name} =" in script


def test_failed_native_operations_are_not_silently_reported_as_capacity_success():
    """Attention operations and simulated database failures invalidate a sweep row."""
    root = Path(__file__).resolve().parents[1]
    runner = (root / "scripts/run_worker_sweep.ps1").read_text(encoding="utf-8")
    native = (root / "src/annotatiathon/native_worker.py").read_text(encoding="utf-8")
    validation = (root / "src/annotatiathon/validation_tasks.py").read_text(encoding="utf-8")
    assert "operation_states.attention" in runner
    assert "operation_errors" in runner
    assert "Valid = -not $integrityFailure" in runner
    assert "AttentionOperations = $attentionCount" in runner
    assert "OperationErrorKinds = $operationErrors.Count" in runner
    assert 'os.environ.get("BENCHMARK_ANNOTATORS") == "300"' in native
    assert 'os.environ.get("BENCHMARK_ANNOTATORS") == "300"' in validation
    assert 'logger.exception("Synthetic publish operation' in native
    assert 'logger.exception("Synthetic correction operation' in native
    assert 'logger.exception("Synthetic validation task' in validation
