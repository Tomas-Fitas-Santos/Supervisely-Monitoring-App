"""Isolated local capacity experiment. Never connects to Supervisely.

Run with Python and the app dependencies plus psutil and Pillow installed:
    python scripts/benchmark_event.py --seconds 120 --latencies 0 0.05 0.2

Uses SQLite WAL, synthetic images/annotations, real HTTP/auth/API handlers and
the production sync_all function. Results are diagnostic, not a PostgreSQL or
Supervisely capacity certification. All runtime data is in .benchmark-data.
"""
import argparse
import concurrent.futures
import json
import os
from pathlib import Path
import socket
import statistics
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import httpx
import psutil
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]


def database(directory):
    engine = create_engine(
        "sqlite:///" + (directory / "event.sqlite").as_posix(),
        connect_args={"check_same_thread": False, "timeout": 60},
    )

    @event.listens_for(engine, "connect")
    def configure(connection, _):
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")

    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def seed(directory):
    from PIL import Image
    from annotatiathon import models as m
    from annotatiathon.security import token_digest

    engine, maker = database(directory)
    m.Base.metadata.create_all(engine)
    media = directory / "media"
    media.mkdir()
    # Ten shared source frames, independently annotated in each of 260 jobs.
    image = Image.frombytes("RGB", (1600, 1200), os.urandom(1600 * 1200 * 3))
    for index in range(10):
        image.save(media / f"frame-{index}.jpg", quality=95)
    with maker.begin() as db:
        db.add(m.Event(id="event", name="Synthetic benchmark"))
        db.flush()
        for index in range(10):
            db.add(m.StaffAccount(id=f"staff-{index}", email=f"monitor{index}@example.test",
                                  name=f"Monitor {index}", roles=["monitor"],
                                  password_hash="disabled-synthetic-account"))
            db.add(m.Person(id=f"monitor-{index}", event_id="event",
                            email=f"monitor{index}@example.test", name=f"Monitor {index}",
                            kind="monitor"))
        db.flush()
        for index in range(10):
            db.add(m.StaffSession(id=token_digest(f"synthetic-token-{index}"),
                                  staff_id=f"staff-{index}", event_id="event",
                                  csrf_token="synthetic-csrf", expires_at=m.epoch() + 3600))
        db.add(m.MediaDataset(id="dataset", event_id="event", name="Synthetic frames",
                              upload_kind="folder", status="ready", root_path=str(directory),
                              created_by="staff-0"))
        db.flush()
        db.add(m.LabelingPlan(id="plan", event_id="event", dataset_id="dataset",
                              jobs_per_team=2, redundancy=1, status="running",
                              created_by="staff-0"))
        for frame in range(10):
            db.add(m.MediaAsset(id=f"asset-{frame}", event_id="event", dataset_id="dataset",
                                relative_path=f"frame-{frame}.jpg", morphology="bird",
                                kind="image", size_bytes=(media / f"frame-{frame}.jpg").stat().st_size,
                                sha256="a" * 64))
        for team in range(130):
            db.add(m.CompetitionTeam(id=f"team-{team}", event_id="event", name=f"Team {team}"))
        db.flush()
        for team in range(130):
            db.add(m.MonitorAssignment(event_id="event", team_id=f"team-{team}",
                                       monitor_person_id=f"monitor-{team % 10}",
                                       assigned_by="staff-0"))
        for index in range(260):
            db.add(m.Person(id=f"person-{index}", event_id="event", name=f"Annotator {index}",
                            email=f"annotator{index}@example.test", kind="participant",
                            team_id=f"team-{index // 2}", supervisely_user_id=1000 + index,
                            membership_status="joined", supervisely_role="annotator",
                            native_verified_at=m.epoch()))
        db.flush()
        for index in range(260):
            db.add(m.LabelingJob(id=f"job-{index}", event_id="event", plan_id="plan",
                                 dataset_id="dataset", team_id=f"team-{index // 2}",
                                 assignee_person_id=f"person-{index}", sequence=index % 2 + 1,
                                 media_ids=[f"asset-{frame}" for frame in range(10)],
                                 frame_count=10, video_count=1, morphology_counts={"bird": 10},
                                 status="in_progress", native_dataset_id=2000 + index))
        db.flush()
        for index in range(260):
            db.add(m.NativeJobBinding(event_id="event", job_id=f"job-{index}",
                                      person_id=f"person-{index}", native_job_id=3000 + index,
                                      native_status="in_progress"))
            for frame in range(10):
                db.add(m.NativeMediaInstance(event_id="event", job_id=f"job-{index}",
                                             media_id=f"asset-{frame}",
                                             native_dataset_id=2000 + index,
                                             native_image_id=index * 10 + frame))
    engine.dispose()


class SimulatedSupervisely:
    def __init__(self, latency):
        self.latency = latency
        self.started = time.monotonic()
        self.calls = {"status": 0, "annotation": 0}
        self.labeling_job = SimpleNamespace(get_info_by_id=self.status)
        self.annotation = SimpleNamespace(download_json=self.annotation_json)

    def submitted(self):
        # 26 ten-frame jobs per minute = 260 annotations/minute. Two cohorts
        # suffice for this short run; later submissions cap at the fixture size.
        return min(260, 26 * (1 + int((time.monotonic() - self.started) / 60)))

    def status(self, identifier):
        self.calls["status"] += 1
        time.sleep(self.latency)
        index = identifier - 3000
        return SimpleNamespace(id=identifier, dataset_id=2000 + index,
                               status="on_review" if index < self.submitted() else "in_progress")

    def annotation_json(self, identifier):
        self.calls["annotation"] += 1
        time.sleep(self.latency)
        return {"size": {"width": 1600, "height": 1200}, "labels": [
            {"classTitle": "bird", "geometryType": "rectangle",
             "points": {"exterior": [[10, 10], [200, 200]], "interior": []}}
            for _ in range(30)
        ]}


def serve(directory, port, latency):
    import uvicorn
    from annotatiathon.app import app
    from annotatiathon.db import get_db
    from annotatiathon.models import SuperviselyConfig
    from annotatiathon.native_worker import sync_all

    _, maker = database(directory)

    def dependency():
        with maker() as db:
            yield db

    app.dependency_overrides[get_db] = dependency
    sdk = SimulatedSupervisely(latency)

    def worker():
        cycle = 0
        while True:
            started = time.monotonic()
            try:
                with maker() as db:
                    result = sync_all(db, sdk, SuperviselyConfig(id=1))
                record = {"cycle": cycle, "seconds": time.monotonic() - started,
                          "elapsed": time.monotonic() - sdk.started,
                          "submitted": sdk.submitted(), "calls": dict(sdk.calls), **result}
            except Exception as exc:
                record = {"error": repr(exc)}
            with (directory / "worker.jsonl").open("a") as stream:
                stream.write(json.dumps(record) + "\n")
            cycle += 1
            time.sleep(12)

    threading.Thread(target=worker, daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=port, access_log=False, log_level="warning")


def percentile(values, fraction):
    return sorted(values)[min(len(values) - 1, int(len(values) * fraction))] if values else None


def process_totals(process):
    # Windows venv python.exe can be a launcher with a separate interpreter child.
    totals = {"cpu": 0, "rss": 0, "private": 0}
    for member in [process, *process.children(recursive=True)]:
        try:
            cpu = member.cpu_times()
            memory = member.memory_info()
            totals["cpu"] += cpu.user + cpu.system
            totals["rss"] += memory.rss
            totals["private"] += getattr(memory, "private", memory.rss)
        except psutil.NoSuchProcess:
            pass
    return totals


def run(seconds, latency):
    directory = ROOT / ".benchmark-data" / f"{time.time_ns()}-{latency}"
    directory.mkdir(parents=True)
    seed(directory)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    environment = dict(os.environ, APP_ENV="local")
    with (directory / "server.log").open("w") as log:
        child = subprocess.Popen([sys.executable, __file__, "--serve", str(directory),
                                  "--port", str(port), "--latencies", str(latency)],
                                 stdout=log, stderr=log, env=environment)
        try:
            url = f"http://127.0.0.1:{port}"
            for _ in range(100):
                if child.poll() is not None:
                    raise RuntimeError((directory / "server.log").read_text())
                try:
                    if httpx.get(url + "/health/live", trust_env=False, timeout=1).status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(0.1)
            else:
                raise RuntimeError("Benchmark API did not start")
            process = psutil.Process(child.pid)
            initial = process_totals(process)
            started = time.monotonic()
            deadline = started + seconds
            samples = []

            def monitor(index):
                requests = []
                with httpx.Client(base_url=url, timeout=60, trust_env=False, cookies={
                    "annotatiathon_session": f"synthetic-token-{index}"
                }) as client:
                    while time.monotonic() < deadline:
                        tick = time.monotonic()
                        # Representative review screen + assignment polling.
                        for path in ["/api/v1/labeling/jobs?limit=2000",
                                     "/api/v1/monitoring/assignments",
                                     f"/api/v1/review/jobs/job-{index * 2}",
                                     "/api/v1/review/datasets/dataset/assets/asset-0/image"]:
                            before = time.monotonic()
                            try:
                                response = client.get(path)
                                requests.append({"path": path, "status": response.status_code,
                                                 "ms": (time.monotonic() - before) * 1000,
                                                 "bytes": len(response.content)})
                            except httpx.HTTPError as exc:
                                requests.append({"path": path, "status": type(exc).__name__,
                                                 "ms": (time.monotonic() - before) * 1000,
                                                 "bytes": 0})
                        time.sleep(max(0, min(5 - (time.monotonic() - tick),
                                              deadline - time.monotonic())))
                return requests

            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
                futures = [pool.submit(monitor, index) for index in range(10)]
                while any(not future.done() for future in futures):
                    memory = process_totals(process)
                    samples.append({"rss_mib": memory["rss"] / 1024**2,
                                    "private_mib": memory["private"] / 1024**2})
                    time.sleep(1)
                requests = [request for future in futures for request in future.result()]
            elapsed = time.monotonic() - started
            cpu_seconds = process_totals(process)["cpu"] - initial["cpu"]
            cycles = [json.loads(line) for line in (directory / "worker.jsonl").read_text().splitlines()]
            snapshots = len(list((directory / "annotations").glob("*/round-0/manifest.json")))
            result = {
                "latency_seconds_per_sdk_call": latency, "elapsed_seconds": elapsed,
                "jobs": 260, "monitors": 10, "frames_per_job": 10,
                "cpu_seconds_server_and_worker": cpu_seconds,
                "average_cpu_cores": cpu_seconds / elapsed,
                "peak_rss_mib": max(item["rss_mib"] for item in samples),
                "peak_private_mib": max(item["private_mib"] for item in samples),
                "requests": len(requests),
                "errors": [request for request in requests if request["status"] != 200],
                "response_p95_ms": percentile([item["ms"] for item in requests], .95),
                "response_p99_ms": percentile([item["ms"] for item in requests], .99),
                "bytes_received": sum(item["bytes"] for item in requests),
                "snapshots_ready": snapshots, "worker_cycles": cycles,
                "directory": str(directory),
            }
            (directory / "requests.json").write_text(json.dumps(requests, indent=2))
            (directory / "result.json").write_text(json.dumps(result, indent=2))
            return result
        finally:
            for descendant in reversed(psutil.Process(child.pid).children(recursive=True)):
                descendant.terminate()
            child.terminate()
            child.wait(timeout=15)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, default=120)
    parser.add_argument("--latencies", type=float, nargs="+", default=[0, .05, .2])
    parser.add_argument("--serve", type=Path)
    parser.add_argument("--port", type=int)
    arguments = parser.parse_args()
    if arguments.serve:
        serve(arguments.serve, arguments.port, arguments.latencies[0])
        return
    results = []
    for latency in arguments.latencies:
        result = run(arguments.seconds, latency)
        results.append(result)
        print(json.dumps(result), flush=True)
    (ROOT / ".benchmark-data" / "latest.json").write_text(json.dumps({
        "host": {"platform": sys.platform, "python": sys.version,
                 "logical_cpus": psutil.cpu_count(), "physical_cpus": psutil.cpu_count(False),
                 "total_ram_gib": psutil.virtual_memory().total / 1024**3},
        "results": results,
    }, indent=2))


if __name__ == "__main__":
    main()
