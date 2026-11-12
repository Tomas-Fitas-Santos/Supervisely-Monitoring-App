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
    assert "up -d --build" not in script
    assert '"nightjar-bench-300-$stamp-p$count-v$Validators"' in script
    assert "down --volumes --remove-orphans" in script
