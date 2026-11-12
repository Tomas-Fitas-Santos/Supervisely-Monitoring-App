"""Production-container event experiment with an isolated Supervisely HTTP simulator.

Used by compose.benchmark.yaml. All accounts, media, API keys and responses are
synthetic. The internal Docker network prevents access to real Supervisely.
"""
import argparse
import asyncio
from collections import Counter
import concurrent.futures
from email import policy
from email.parser import BytesParser
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import random
import ssl
import shutil
import threading
import time
import zipfile

import httpx
from sqlalchemy import select, func, update, insert, delete

DATA = Path("/data")
RESULTS = DATA / "results"
PASSWORD = "SyntheticBenchmarkPassword!"
FRAMES = int(os.environ.get("BENCHMARK_FRAMES", "30"))
ANNOTATORS = int(os.environ.get("BENCHMARK_ANNOTATORS", "300"))
MONITORS = int(os.environ.get("BENCHMARK_MONITORS", "12"))
SECONDS_PER_FRAME = int(os.environ.get("BENCHMARK_SECONDS_PER_FRAME", "90"))
UPLOAD = os.environ.get("BENCHMARK_UPLOAD") == "1"
PROFILE_PATH = os.environ.get("BENCHMARK_PROFILE_PATH", "").strip()


def certificates():
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    tls = DATA / "tls"
    tls.mkdir(exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Synthetic benchmark")])
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=7))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName([
            x509.DNSName("web"), x509.DNSName("supervisely-sim")
        ]), critical=False).sign(key, hashes.SHA256())
    )
    (tls / "ca.pem").write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    (tls / "key.pem").write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))


def seed(frames):
    import benchmark_event
    from annotatiathon import models as m
    from annotatiathon.credentials import encrypt_token
    from annotatiathon.db import session_factory
    from annotatiathon.security import password_hash

    maker = session_factory()
    with maker() as db:
        if db.get(m.SuperviselyConfig, 1) is not None:
            print("Reusing isolated benchmark fixture", flush=True)
            return
    RESULTS.mkdir(exist_ok=True)
    certificates()
    root = DATA / "event" / "dataset"
    root.mkdir(parents=True, exist_ok=True)
    engine = maker.kw["bind"]
    # Reuse the previous fixture builder against PostgreSQL. Production Alembic
    # migrations have already created this fresh, isolated database.
    benchmark_event.database = lambda directory: (engine, maker)
    benchmark_event.seed(root, annotators=ANNOTATORS, monitors=MONITORS)
    password = password_hash(PASSWORD)
    with maker.begin() as db:
        for staff in db.scalars(select(m.StaffAccount)):
            staff.password_hash = password
        db.add(m.StaffAccount(id="organiser", email="organiser@example.test",
                              name="Synthetic organiser", roles=["organiser"], password_hash=password))
        dataset = db.get(m.MediaDataset, "dataset")
        dataset.native_project_id = 41
        for frame in range(10, frames):
            target = root / "media" / f"frame-{frame}.jpg"
            shutil.copyfile(root / "media" / f"frame-{frame % 10}.jpg", target)
            db.add(m.MediaAsset(id=f"asset-{frame}", event_id="event", dataset_id="dataset",
                                relative_path=target.name, morphology="bird", kind="image",
                                size_bytes=target.stat().st_size, sha256=hashlib.sha256(target.read_bytes()).hexdigest()))
        db.flush()
        for asset in db.scalars(select(m.MediaAsset)):
            source = root / "media" / asset.relative_path
            # Unique valid JPEG files prevent SDK hash deduplication from turning
            # the binary-transfer case into a one-image upload.
            with source.open("ab") as stream:
                stream.write(f"\nsynthetic-frame-{asset.id}\n".encode())
            asset.sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
            asset.size_bytes = source.stat().st_size
        db.execute(delete(m.NativeMediaInstance))
        for index in range(ANNOTATORS):
            job = db.get(m.LabelingJob, f"job-{index}")
            job.media_ids = [f"asset-{frame}" for frame in range(frames)]
            job.frame_count = frames
            job.morphology_counts = {"bird": frames}
            db.add_all([m.NativeMediaInstance(event_id="event", job_id=job.id,
                                             media_id=f"asset-{frame}", native_dataset_id=2000 + index,
                                             native_image_id=100000 + index * frames + frame)
                        for frame in range(frames)])
        db.get(m.LabelingPlan, "plan").jobs_per_team = 4
        for index in range(ANNOTATORS, ANNOTATORS * 2):
            person_index = index - ANNOTATORS
            db.add(m.LabelingJob(
                id=f"job-{index}", event_id="event", plan_id="plan", dataset_id="dataset",
                team_id=f"team-{person_index // 2}", assignee_person_id=f"person-{person_index}",
                sequence=3 + person_index % 2, media_ids=[f"asset-{frame}" for frame in range(frames)],
                frame_count=frames, video_count=1, morphology_counts={"bird": frames},
                native_dataset_id=None if UPLOAD else 2000 + index, status="queued",
            ))
        db.add(m.SuperviselyConfig(id=1, server_url="https://supervisely-sim", team_id=125980,
                                   workspace_id=2001, verified=True,
                                   token_ciphertext=encrypt_token("synthetic-supervisely-key")))
    for directory, _, files in os.walk(DATA):
        os.chown(directory, 10001, 10001)
        for name in files:
            os.chown(Path(directory) / name, 10001, 10001)
    print(json.dumps({"seeded": True, "annotators": ANNOTATORS, "monitors": MONITORS,
                      "active_jobs": ANNOTATORS, "queued_jobs": ANNOTATORS}), flush=True)


def annotation(identifier):
    from annotatiathon.annotation_schema import ANNOTATION_CLASSES

    objects = []
    for bird in range(3):
        for title, shape, _, _ in ANNOTATION_CLASSES:
            if title.endswith("_occ"):
                continue
            points = [[50 + bird * 250, 60], [200 + bird * 250, 220]]
            if shape == "point":
                points = points[:1]
            objects.append({"id": identifier * 100 + len(objects), "classTitle": title,
                            "geometryType": shape, "tags": [], "description": "",
                            "points": {"exterior": points, "interior": []}})
    return {"description": "Synthetic bird annotations", "tags": [],
            "size": {"width": 1600, "height": 1200}, "objects": objects}


def simulate():
    from fastapi import FastAPI, Request, HTTPException
    from fastapi.responses import JSONResponse
    import uvicorn
    from supervisely.api.labeling_job_api import LabelingJobApi
    from supervisely.api.project_api import ProjectApi
    from supervisely.api.dataset_api import DatasetApi
    from supervisely.api.image_api import ImageApi
    from supervisely.api.workspace_api import WorkspaceApi
    from supervisely.api.team_api import TeamApi
    from annotatiathon.annotation_schema import merge_annotation_meta

    app = FastAPI()
    rng = random.Random(20261110)
    state = {"started": None, "latency": .05, "fail_rate": 0.0,
             "calls": Counter(), "failures": 0, "unhandled": [], "published": 0,
             "binary_upload_bytes": 0, "binary_upload_files": 0}
    remote_datasets = set(range(ANNOTATORS if UPLOAD else ANNOTATORS * 2))
    uploaded_images = {}
    uploaded_hashes = set()
    submitted_times = {}
    metadata, _ = merge_annotation_meta({"projectType": "images", "classes": [], "tags": []})

    def info(api_class, **values):
        result = {}
        for field in api_class.info_sequence():
            path = field[0] if isinstance(field, tuple) else [field]
            alias = field[1] if isinstance(field, tuple) else field
            if not path:
                continue
            cursor = result
            for key in path[:-1]:
                cursor = cursor.setdefault(key, {})
            cursor[path[-1]] = values.get(alias)
        return result

    def project():
        return info(ProjectApi, id=41, name="Nightjar-event-dataset", workspaceId=2001,
                    teamId=125980, type="images", itemsCount=5200, size=0, customData={})

    def dataset(index):
        return info(DatasetApi, id=2000 + index, name=f"Nightjar-job-{index}",
                    projectId=41, itemsCount=FRAMES, imagesCount=FRAMES, size=0, customData={})

    def images(index):
        if UPLOAD and index >= ANNOTATORS:
            return uploaded_images.get(index, [])
        return [info(ImageApi, id=100000 + index * FRAMES + frame, name=f"asset-{frame}.jpg",
                     datasetId=2000 + index, width=1600, height=1200, size=2250000,
                     meta={}, customData={}, labelsCount=15, tags=[])
                for frame in range(FRAMES)]

    def job(index):
        if state["started"] is None:
            status = "in_progress"
        else:
            due = submitted_times.get(index, float("inf"))
            status = "on_review" if time.monotonic() >= due else "in_progress"
        return info(LabelingJobApi, id=3000 + index, name=f"Synthetic job {index}",
                    teamId=125980, workspaceId=2001, workspace_name="Synthetic workspace",
                    projectId=41, project_name="Nightjar-event-dataset",
                    datasetId=2000 + index, dataset_name=f"Nightjar-job-{index}",
                    assigned_to_id=1000 + index % ANNOTATORS,
                    assigned_to_login=f"annotator{index % ANNOTATORS}",
                    status=status, disabled=False, imagesCount=FRAMES,
                    finishedImagesCount=FRAMES if status == "on_review" else 0,
                    rejectedImagesCount=0, acceptedImagesCount=0, progressImagesCount=0,
                    classes_to_label=[], tags_to_label=[], images_range={"start": 1, "end": FRAMES},
                    filter_images_by_tags=[], entities=[])

    def page(items, body):
        page_number = body.get("page", 1)
        page_size = body.get("perPage", 500)
        return {"entities": items[(page_number - 1) * page_size:page_number * page_size],
                "total": len(items), "perPage": page_size, "pagesCount": max(1, (len(items) + page_size - 1) // page_size)}

    @app.post("/control/start")
    async def start(request: Request):
        body = await request.json()
        state["latency"] = body.get("latency", .05)
        state["fail_rate"] = body.get("fail_rate", 0)
        state["started"] = time.monotonic()
        state["burst"] = body.get("burst", False)
        for index in range(ANNOTATORS):
            offset = 0 if state["burst"] else index * FRAMES * SECONDS_PER_FRAME / ANNOTATORS
            submitted_times[index] = state["started"] + offset
        return {"started": True}

    @app.get("/control/stats")
    async def stats():
        now = time.monotonic()
        return {**state, "calls": dict(state["calls"]),
                "submitted": sum(due <= now for due in submitted_times.values())}

    @app.post("/public/api/v3/{method:path}")
    async def request_api(method: str, request: Request):
        state["calls"][method] += 1
        await asyncio.sleep(state["latency"] * rng.uniform(.5, 1.5))
        if rng.random() < state["fail_rate"]:
            state["failures"] += 1
            return JSONResponse({"error": "Synthetic transient failure"}, status_code=503)
        if method == "images.bulk.upload":
            from supervisely.io.fs import get_bytes_hash
            raw = await request.body()
            message = BytesParser(policy=policy.default).parsebytes(
                f"Content-Type: {request.headers['content-type']}\r\nMIME-Version: 1.0\r\n\r\n".encode() + raw)
            result = []
            for part in message.iter_parts():
                content = part.get_payload(decode=True)
                value = get_bytes_hash(content)
                uploaded_hashes.add(value)
                state["binary_upload_bytes"] += len(content)
                state["binary_upload_files"] += 1
                result.append({"hash": value})
            return result
        body = await request.json()
        if method == "images.internal.hashes.list":
            return [value for value in body if value in uploaded_hashes]
        if method == "images.bulk.add":
            index = body["datasetId"] - 2000
            results = []
            for item in body["images"]:
                frame = int(item["title"].split("-")[-1].split(".")[0])
                result = info(ImageApi, id=100000 + index * FRAMES + frame,
                              name=item["title"], datasetId=body["datasetId"], width=1600,
                              height=1200, size=2250000, meta={}, customData={}, tags=[])
                uploaded_images.setdefault(index, []).append(result)
                results.append(result)
            return results
        if method == "jobs.info":
            return job(body["id"] - 3000)
        if method == "annotations.info":
            identifier = body["imageId"]
            return {"imageId": identifier, "imageName": "synthetic.jpg",
                    "datasetId": 2000 + (identifier - 100000) // FRAMES,
                    "annotation": annotation(identifier), "createdAt": None, "updatedAt": None}
        if method == "workspaces.info":
            return info(WorkspaceApi, id=2001, name="Synthetic workspace", teamId=125980)
        if method == "projects.info":
            return project()
        if method == "projects.meta":
            return metadata
        if method == "projects.meta.update":
            metadata.update(body.get("meta", {}))
            return {}
        if method == "datasets.info":
            return dataset(body["id"] - 2000)
        if method == "datasets.list":
            return page([dataset(index) for index in sorted(remote_datasets)], body)
        if method == "datasets.add":
            index = int(body["name"].split("-")[-1])
            remote_datasets.add(index)
            return dataset(index)
        if method == "images.list":
            return page(images(body["datasetId"] - 2000), body)
        if method == "jobs.list":
            index = next((item["value"] - 2000 for item in body.get("filter", [])
                          if item["field"] == "datasetId"), None)
            items = [job(index)] if index is not None and index in submitted_times else []
            return page(items, body)
        if method == "users.info":
            return {"teams": [{"id": 125980, "name": "Synthetic team", "role": "annotator", "roleId": 5}]}
        if method == "jobs.add":
            index = body["datasetId"] - 2000
            submitted_times[index] = time.monotonic() + FRAMES * 60
            state["published"] += 1
            return [job(index)]
        state["unhandled"].append({"method": method, "body": body})
        raise HTTPException(status_code=400, detail=f"Unsupported simulator method {method}")

    uvicorn.run(app, host="0.0.0.0", port=443, ssl_keyfile=str(DATA / "tls/key.pem"),
                ssl_certfile=str(DATA / "tls/ca.pem"), access_log=False)


def database_summary():
    from annotatiathon import models as m
    from annotatiathon.db import session_factory
    with session_factory()() as db:
        return {
            "job_states": dict(db.execute(select(m.LabelingJob.status, func.count())
                                            .group_by(m.LabelingJob.status)).all()),
            "operation_states": dict(db.execute(select(m.NativeOperation.status, func.count())
                                                  .group_by(m.NativeOperation.status)).all()),
            "operation_errors": list(db.execute(select(m.NativeOperation.error_code,
                                                         func.count()).where(
                m.NativeOperation.error_code.is_not(None)
            ).group_by(m.NativeOperation.error_code)).mappings().all()),
            "reviews": db.scalar(select(func.count()).select_from(m.JobReview)),
            "snapshots": len(list((DATA / "event/dataset/annotations").glob("*/round-*/manifest.json"))),
        }


def import_dataset(client, call):
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
        for source in sorted((DATA / "event/dataset/media").glob("*.jpg")):
            zipped.write(source, f"images/bird/{source.name}")
    content = archive.getvalue()
    response = call(client, "POST", "/api/v1/datasets/uploads", json={
        "name": "Synthetic lifecycle import", "upload_kind": "zip"})
    response.raise_for_status()
    identifier = response.json()["id"]
    for offset in range(0, len(content), 8 * 1024**2):
        response = call(client, "PUT", f"/api/v1/datasets/uploads/{identifier}/chunk", params={
            "relative_path": "source.zip", "offset": offset, "total": len(content)},
            content=content[offset:offset + 8 * 1024**2],
            headers={"Content-Type": "application/octet-stream"})
        response.raise_for_status()
    started = time.monotonic()
    response = call(client, "POST", f"/api/v1/datasets/{identifier}/finalize")
    response.raise_for_status()
    return {"archive_bytes": len(content), "finalize_seconds": time.monotonic() - started,
            "dataset": response.json()}


def export_dataset(client, call):
    started = time.monotonic()
    response = call(client, "POST", "/api/v1/events/event/archive", json={
        "confirmation": "Synthetic benchmark"})
    response.raise_for_status()
    response = call(client, "POST", "/api/v1/native/export/prepare")
    response.raise_for_status()
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        response = call(client, "GET", "/api/v1/native/export")
        response.raise_for_status()
        status = response.json()
        if status["status"] in {"attention", "blocked"}:
            return {"failed": True, "status": status}
        if status["ready_to_download"]:
            response = call(client, "GET", "/api/v1/native/export/download")
            response.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                bad_member = archive.testzip()
                members = len(archive.namelist())
            return {"seconds": time.monotonic() - started, "bytes": len(response.content),
                    "members": members, "corrupt_member": bad_member}
        time.sleep(3)
    return {"failed": True, "error": "Export exceeded ten-minute timeout"}


def historical_fixture():
    from annotatiathon import models as m
    from annotatiathon.db import session_factory
    maker = session_factory()
    with maker.begin() as db:
        existing = db.scalar(select(func.count()).select_from(m.LabelingJob))
        count = max(0, ANNOTATORS * 12 * 3600 // (FRAMES * SECONDS_PER_FRAME) - existing)
        db.add(m.LabelingPlan(id="history", event_id="event", dataset_id="dataset",
                              jobs_per_team=max(1, count // (ANNOTATORS // 2)),
                              redundancy=1, status="completed", created_by="organiser"))
    for start in range(0, count, 500):
        with maker.begin() as db:
            jobs = [{"id": f"history-{index}", "event_id": "event", "plan_id": "history",
                     "dataset_id": "dataset", "team_id": f"team-{index % (ANNOTATORS // 2)}",
                     "assignee_person_id": f"person-{(index % (ANNOTATORS // 2)) * 2 + (index // (ANNOTATORS // 2)) % 2}",
                     "sequence": index // 130 + 1,
                     "media_ids": [f"asset-{frame}" for frame in range(FRAMES)], "frame_count": FRAMES,
                     "video_count": 1, "morphology_counts": {"bird": FRAMES}, "status": "completed",
                     "review_status": "accepted", "native_dataset_id": 1000000 + index}
                    for index in range(start, min(count, start + 500))]
            db.execute(insert(m.LabelingJob), jobs)
            db.execute(insert(m.NativeJobBinding), [{
                "event_id": "event", "job_id": job["id"], "person_id": job["assignee_person_id"],
                "native_job_id": 1000000 + start + offset, "native_status": "completed",
            } for offset, job in enumerate(jobs)])
            db.execute(insert(m.NativeMediaInstance), [{
                "event_id": "event", "job_id": job["id"], "media_id": f"asset-{frame}",
                "native_dataset_id": job["native_dataset_id"],
                "native_image_id": (1000000 + start + offset) * FRAMES + frame,
            } for offset, job in enumerate(jobs) for frame in range(FRAMES)])
    return {"added_completed_jobs": count, "total_jobs": existing + count,
            "equivalent_annotation_completions": (existing + count) * FRAMES}


def profile_queries():
    from sqlalchemy import event
    from annotatiathon import models as m
    from annotatiathon.auth import AuthContext
    from annotatiathon.db import session_factory
    from annotatiathon.labeling_api import list_jobs
    maker = session_factory()
    statements = Counter()

    def count_queries(connection, cursor, statement, parameters, context, many):
        statements["total"] += 1
        if "FROM people" in statement:
            statements["people"] += 1

    engine = maker.kw["bind"]
    with maker() as db:
        staff = db.get(m.StaffAccount, "staff-0")
        session = db.scalar(select(m.StaffSession).where(m.StaffSession.staff_id == staff.id).limit(1))
        event.listen(engine, "before_cursor_execute", count_queries)
        try:
            started = time.monotonic()
            response = list_jobs(ctx=AuthContext(staff=staff, session=session), db=db, limit=2000)
            result = {"returned_jobs": len(response["items"]), "sql": dict(statements),
                      "seconds_without_competing_monitor_requests": time.monotonic() - started}
        finally:
            event.remove(engine, "before_cursor_execute", count_queries)
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "query-profile.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result), flush=True)


def load(seconds, latency, fail_rate):
    context = ssl.create_default_context(cafile=str(DATA / "tls/ca.pem"))
    records = []
    lock = threading.Lock()
    started = time.monotonic()
    deadline = started + seconds
    phases = {}

    def call(client, method, path, **kwargs):
        before = time.monotonic()
        try:
            response = client.request(method, path, **kwargs)
            entry = {"method": method, "path": path, "status": response.status_code,
                     "ms": (time.monotonic() - before) * 1000, "bytes": len(response.content)}
        except httpx.HTTPError as exc:
            response = None
            entry = {"method": method, "path": path, "status": type(exc).__name__,
                     "ms": (time.monotonic() - before) * 1000, "bytes": 0}
        with lock:
            records.append(entry)
        return response

    if seconds >= 300:
        with httpx.Client(base_url="https://web", verify=context, trust_env=False, timeout=120) as organiser:
            response = call(organiser, "POST", "/api/v1/auth/login", json={
                "email": "organiser@example.test", "password": PASSWORD})
            response.raise_for_status()
            organiser.headers["X-CSRF-Token"] = response.json()["csrf_token"]
            phases["import"] = import_dataset(organiser, call)
    with httpx.Client(verify=context, trust_env=False, timeout=120) as control:
        for attempt in range(60):
            try:
                if control.get("https://supervisely-sim/control/stats").status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(1)
        control.post("https://supervisely-sim/control/start",
                     json={"latency": latency, "fail_rate": fail_rate,
                           "burst": os.environ.get("BENCHMARK_BURST") == "1"}).raise_for_status()
    deadline = time.monotonic() + seconds

    def monitor(index):
        completed = set()
        with httpx.Client(base_url="https://web", verify=context, trust_env=False, timeout=120) as client:
            login = call(client, "POST", "/api/v1/auth/login", json={
                "email": f"monitor{index}@example.test", "password": PASSWORD})
            if login is None or login.status_code != 200:
                return {"monitor": index, "login_failed": True}
            client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
            selection = call(client, "POST", "/api/v1/events/event/select")
            selection.raise_for_status()
            while time.monotonic() < deadline:
                tick = time.monotonic()
                response = call(client, "GET", "/api/v1/labeling/jobs?limit=2000")
                call(client, "GET", "/api/v1/monitoring/assignments")
                if response is not None and response.status_code == 200:
                    owned = [job for job in response.json()["items"]
                             if int(job["team_id"].split("-")[-1]) % 10 == index]
                    pending = None
                    for candidate in owned:
                        if candidate["status"] != "on_review" or candidate["id"] in completed:
                            continue
                        review = call(client, "GET", f"/api/v1/review/jobs/{candidate['id']}")
                        if review is not None and review.status_code == 200 and review.json()["annotation_snapshot_ready"]:
                            pending = candidate
                            break
                    if pending:
                        job_id = pending["id"]
                        if review is not None and review.status_code == 200 and review.json()["annotation_snapshot_ready"]:
                            call(client, "GET", f"/api/v1/labeling/jobs/{job_id}/media?page=1&size=50")
                            scores_saved = True
                            for frame in range(FRAMES):
                                media_id = f"asset-{frame}"
                                call(client, "GET", f"/api/v1/review/datasets/dataset/assets/{media_id}/image")
                                call(client, "GET", f"/api/v1/review/jobs/{job_id}/annotations/{media_id}")
                                score = call(client, "PUT", f"/api/v1/review/jobs/{job_id}/frames", json={
                                    "media_id": media_id, "verdict": "good", "correct_keypoints": 4})
                                scores_saved = scores_saved and score is not None and score.status_code == 200
                            if scores_saved:
                                decision = call(client, "POST", f"/api/v1/review/jobs/{job_id}/decision",
                                                json={"decision": "accept"})
                                if decision is not None and decision.status_code == 200:
                                    completed.add(job_id)
                    # Continue browsing representative originals even while
                    # submitted annotations are waiting for the worker.
                    call(client, "GET", "/api/v1/review/datasets/dataset/assets/asset-1/image")
                time.sleep(max(0, min(5 - (time.monotonic() - tick), deadline - time.monotonic())))
        return {"monitor": index, "accepted": len(completed)}

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        monitors = list(pool.map(monitor, range(10)))
    with httpx.Client(verify=context, trust_env=False, timeout=120) as control:
        simulator = control.get("https://supervisely-sim/control/stats").json()
    event_summary = database_summary()
    if seconds >= 300:
        with httpx.Client(base_url="https://web", verify=context, trust_env=False, timeout=120) as organiser:
            response = call(organiser, "POST", "/api/v1/auth/login", json={
                "email": "organiser@example.test", "password": PASSWORD})
            response.raise_for_status()
            organiser.headers["X-CSRF-Token"] = response.json()["csrf_token"]
            phases["export"] = export_dataset(organiser, call)
        phases["history_fixture"] = historical_fixture()

        def history_reader(index):
            with httpx.Client(base_url="https://web", verify=context, trust_env=False, timeout=120) as client:
                response = call(client, "POST", "/api/v1/auth/login", json={
                    "email": f"monitor{index}@example.test", "password": PASSWORD})
                response.raise_for_status()
                client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
                call(client, "POST", "/api/v1/events/event/select").raise_for_status()
                response = call(client, "GET", "/api/v1/labeling/jobs?limit=2000")
                response.raise_for_status()
                rows = response.json()["items"]
                if len(rows) != 2000 or not any(row["plan_id"] == "history" for row in rows):
                    raise RuntimeError("Historical workload did not return the populated target event")
                return {"monitor": index, "status": response.status_code, "returned_jobs": len(rows)}
        history_started = time.monotonic()
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
            phases["history_reads"] = {"requests": list(pool.map(history_reader, range(10)))}
        phases["history_reads"]["seconds"] = time.monotonic() - history_started
    latencies = sorted(item["ms"] for item in records)
    result = {
        "duration_seconds": time.monotonic() - started,
        "frames_per_job": FRAMES,
        "latency_seconds": latency, "failure_rate": fail_rate,
        "requests": len(records), "status_counts": dict(Counter(str(item["status"]) for item in records)),
        "p95_ms": latencies[int(len(latencies) * .95)] if latencies else None,
        "p99_ms": latencies[int(len(latencies) * .99)] if latencies else None,
        "transferred_bytes": sum(item["bytes"] for item in records),
        "monitors": monitors, "simulator": simulator, "database": event_summary, "phases": phases,
    }
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "requests.json").write_text(json.dumps(records, indent=2))
    (RESULTS / "result.json").write_text(json.dumps(result, indent=2, default=str))
    print(json.dumps(result, default=str), flush=True)


def history_load():
    context = ssl.create_default_context(cafile=str(DATA / "tls/ca.pem"))
    records = []
    lock = threading.Lock()

    def reader(index):
        with httpx.Client(base_url="https://web", verify=context, trust_env=False, timeout=180) as client:
            def request(method, path, **kwargs):
                response = client.request(method, path, **kwargs)
                with lock:
                    records.append({"method": method, "path": path, "status": response.status_code,
                                    "ms": response.elapsed.total_seconds() * 1000,
                                    "bytes": len(response.content)})
                response.raise_for_status()
                return response

            response = request("POST", "/api/v1/auth/login", json={
                "email": f"monitor{index}@example.test", "password": PASSWORD})
            client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
            request("POST", "/api/v1/events/event/select")
            response = request("GET", "/api/v1/labeling/jobs?limit=2000")
            rows = response.json()["items"]
            historical = sum(row["plan_id"] == "history" for row in rows)
            if len(rows) != 2000 or historical == 0:
                raise RuntimeError("History test queried an empty or wrong event")
            return {"monitor": index, "status": response.status_code, "returned_jobs": len(rows),
                    "returned_historical_jobs": historical, "ms": response.elapsed.total_seconds() * 1000}

    started = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        clients = list(pool.map(reader, range(10)))
    result = {"mode": "history", "frames_per_job": FRAMES, "duration_seconds": time.monotonic() - started,
              "requests": len(records), "status_counts": dict(Counter(str(row["status"]) for row in records)),
              "clients": clients, "p95_ms": max(row["ms"] for row in clients)}
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "requests.json").write_text(json.dumps(records, indent=2))
    (RESULTS / "result.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["seed", "simulate", "load", "summary", "profile", "history"])
    parser.add_argument("--seconds", type=int, default=600)
    parser.add_argument("--frames", type=int, default=FRAMES)
    parser.add_argument("--latency", type=float, default=.05)
    parser.add_argument("--fail-rate", type=float, default=0)
    args = parser.parse_args()
    if args.mode == "seed":
        seed(args.frames)
    elif args.mode == "simulate":
        simulate()
    elif args.mode == "load":
        load(args.seconds, args.latency, args.fail_rate)
    elif args.mode == "profile":
        profile_queries()
    elif args.mode == "history":
        history_load()
    else:
        print(json.dumps(database_summary(), default=str))


if __name__ == "__main__":
    main()
