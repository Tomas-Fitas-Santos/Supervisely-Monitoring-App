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
