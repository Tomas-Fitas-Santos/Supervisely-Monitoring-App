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
FULL_FOLLOWUPS = max(1, ANNOTATORS // 5)
SECONDS_PER_FRAME = int(os.environ.get("BENCHMARK_SECONDS_PER_FRAME", "90"))
UPLOAD = os.environ.get("BENCHMARK_UPLOAD") == "1"
PHASE = os.environ.get("BENCHMARK_PHASE", "turnaround")
INCOMPLETE_RATE = float(os.environ.get("BENCHMARK_INCOMPLETE_RATE", "0.20"))
if PHASE not in {"initial", "turnaround", "full"} or not 0 <= INCOMPLETE_RATE <= 1:
    raise ValueError("Invalid benchmark phase or incomplete rate")
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
        # The isolated stack uses production authentication rules. Every test
        # account must match the persistent approved-email table, otherwise
        # the synthetic load is rejected at login before benchmarking begins.
        for staff in db.scalars(select(m.StaffAccount)):
            staff.password_hash = password
            staff.roles = ["organiser"]
            db.add(m.ApprovedStaffEmail(
                email=staff.email, role="organiser", enabled=True))
        db.add(m.StaffAccount(id="organiser", email="organiser@example.test",
                              name="Synthetic organiser", roles=["organiser"], password_hash=password))
        db.add(m.ApprovedStaffEmail(
            email="organiser@example.test", role="organiser", enabled=True))
        dataset = db.get(m.MediaDataset, "dataset")
        dataset.native_project_id = 41
        if PHASE == "full":
            # The roster is real, but no jobs are in progress before import,
            # mirror, plan creation and launch through the application API.
            db.get(m.LabelingPlan, "plan").status = "completed"
            for person in db.scalars(select(m.Person).where(
                m.Person.kind == "participant",
            )):
                person.check_in_at = m.epoch()
            for previous in db.scalars(select(m.LabelingJob)):
                previous.status = "completed"
                previous.review_status = "accepted"
        if PHASE == "initial":
            # No queue is released before the timed burst. The load process
            # atomically activates the plan and enqueues exactly 300 jobs.
            db.get(m.LabelingPlan, "plan").status = "approved"
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
        if PHASE == "initial":
            for index in range(ANNOTATORS):
                job = db.get(m.LabelingJob, f"job-{index}")
                job.status = "queued"
                job.review_status = "unreviewed"
                db.execute(delete(m.NativeJobBinding).where(
                    m.NativeJobBinding.job_id == job.id))
        else:
            # Exercise the official frame-completeness/auto-replacement path
            # rather than the legacy monitor accept-whole-job endpoints.
            plan = db.get(m.LabelingPlan, "plan")
            plan.queue_seed = "synthetic-throughput-burst"
            dataset.native_dataset_id = 2000
            dataset.frame_count = FRAMES
            dataset.image_count = 0
            db.add(m.NativeOperation(
                event_id="event", kind="mirror_dataset", target_id="dataset",
                status="done",
            ))
            video = m.MediaVideo(
                id="synthetic-video", event_id="event", dataset_id="dataset",
                video_group="synthetic-video", extraction_policy="all_frames",
                frame_count=FRAMES,
            )
            db.add(video)
            db.flush()
            for frame in range(FRAMES):
                asset = db.get(m.MediaAsset, f"asset-{frame}")
                asset.video_id = video.id
                asset.video_group = video.video_group
                asset.frame_index = frame
                db.add(m.NativeSourceMedia(
                    event_id="event", dataset_id="dataset",
                    media_id=asset.id, native_dataset_id=2000,
                    native_image_id=100000 + frame,
                ))
            for index in range(ANNOTATORS):
                person = db.get(m.Person, f"person-{index}")
                person.check_in_at = m.epoch()
                original = db.get(m.LabelingJob, f"job-{index}")
                original.release_mode = "shared_primary"
                db.add(m.WorkPacket(
                    job_id=original.id, event_id="event", person_id=person.id,
                ))
                # Synthetic source frames are reused solely to measure the
                # orchestration capacity of 300 distinct worker assignments.
                # The production queue uses unique real video-frame blocks.
                db.add(m.WorkBlock(
                    event_id="event", plan_id="plan", video_id=video.id,
                    ordinal=index, frame_count=FRAMES,
                    media_ids=[f"asset-{j}" for j in range(FRAMES)],
                    repeat_media_ids=[],
                ))
        # Do not precreate legacy follow-up jobs. The official 30-frame
        # turnaround fixture allocates fresh jobs from WorkBlocks after
        # completeness validation; the initial-release fixture stops at 300.
        db.add(m.SuperviselyConfig(id=1, server_url="https://supervisely-sim", team_id=125980,
                                   workspace_id=2001, verified=True,
                                   token_ciphertext=encrypt_token("synthetic-supervisely-key")))
    for directory, _, files in os.walk(DATA):
        os.chown(directory, 10001, 10001)
        for name in files:
            os.chown(Path(directory) / name, 10001, 10001)
    print(json.dumps({"seeded": True, "phase": PHASE, "annotators": ANNOTATORS,
                      "monitors": MONITORS, "original_jobs": ANNOTATORS,
                      "queued_followups_fixture": 0}), flush=True)


def annotation(identifier):
    from annotatiathon.annotation_schema import ANNOTATION_CLASSES

    objects = []
    # A structurally valid single-bird frame: exactly one box and one
    # visible-or-occluded instance of each of the four anatomical landmarks.
    # The earlier 3-bird fixture was always rejected by the real validator.
    for title, shape, _, _ in ANNOTATION_CLASSES:
        if title.endswith("_occ"):
            continue
        points = [[50, 60], [200, 220]]
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
    from annotatiathon.worker_timing import endpoint_group

    # Only aggregate p50/p95/p99 timings are read. The original real-worker
    # trace remains off the isolated benchmark network and never gets mounted.
    profile = {}
    if PROFILE_PATH:
        profile = json.loads(Path(PROFILE_PATH).read_text(encoding="utf-8"))
        if not isinstance(profile, dict):
            raise ValueError("Benchmark profile must be a category-to-latency JSON object.")

    app = FastAPI()
    rng = random.Random(20261110)
    state = {"started": None, "phase": PHASE, "incomplete_rate": INCOMPLETE_RATE,
             "latency": .05, "fail_rate": 0.0,
             "profile_scale": 1.0, "timeout_rate": 0.0, "timeouts": 0,
             "assumed_publish_ms": 1000,
             "calls": Counter(), "failures": 0, "unhandled": [], "published": 0,
             "binary_upload_bytes": 0, "binary_upload_files": 0}
    remote_datasets = set(range(ANNOTATORS if UPLOAD else ANNOTATORS * 2))
    uploaded_images = {}
    uploaded_hashes = set()
    submitted_times = {}
    created_jobs = {}
    next_native_job_id = 1_000_000
    next_project_id = 10_000
    next_dataset_id = 850_000
    next_image_id = 20_000_000
    projects_created = {}
    datasets_created = {}
    corrected_images = set()
    metadata, _ = merge_annotation_meta({"projectType": "images", "classes": [], "tags": []})
    # Supervisely's labeling-job annotation reader resolves returned figure IDs
    # through project metadata; class identities must be present in the simulator.
    for ordinal, cls in enumerate(metadata["classes"], 1):
        cls["id"] = 40000 + ordinal
    class_ids = {cls["title"]: cls["id"] for cls in metadata["classes"]}
    # LabelingJobApi._convert_json_info expects class records, not strings.
    # A string yields HTTP 200 but raises TypeError while decoding jobs.info.
    classes_to_label = [{"title": title} for title in class_ids]

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
        if index in uploaded_images or index >= ANNOTATORS * 2:
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
                    classes_to_label=classes_to_label, tags_to_label=[], images_range={"start": 1, "end": FRAMES},
                    filter_images_by_tags=[], entities=[])

    def created_job(data):
        count = data["images_count"]
        status = "on_review" if time.monotonic() >= data["due"] else "in_progress"
        return info(LabelingJobApi, id=data["id"], name=data["name"],
                    teamId=125980, workspaceId=2001, workspace_name="Synthetic workspace",
                    projectId=41, project_name="Nightjar-event-dataset",
                    datasetId=data["dataset_id"],
                    dataset_name=f"Nightjar-job-{data['dataset_id'] - 2000}",
                    assigned_to_id=data["user_id"],
                    assigned_to_login=f"annotator{(data['user_id'] - 1000) % ANNOTATORS}",
                    status=status, disabled=False, imagesCount=count,
                    finishedImagesCount=count if status == "on_review" else 0,
                    rejectedImagesCount=0, acceptedImagesCount=0, progressImagesCount=0,
                    classes_to_label=classes_to_label, tags_to_label=[],
                    images_range={"start": 1, "end": count},
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
        state["profile_scale"] = max(.1, min(10.0, float(body.get("profile_scale", 1))))
        state["timeout_rate"] = max(0.0, min(.25, float(body.get("timeout_rate", 0))))
        state["assumed_publish_ms"] = max(100, min(20000,
            float(body.get("assumed_publish_ms", 1000))))
        state["started"] = time.monotonic()
        state["started_unix_ms"] = round(time.time() * 1000)
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
        category = endpoint_group(method)
        observed = profile.get(category, {})
        if isinstance(observed, dict) and observed.get("count", 0) >= 1:
            p50 = max(0, float(observed.get("p50_ms", 0)))
            p95 = max(p50, float(observed.get("p95_ms", p50)))
            p99 = max(p95, float(observed.get("p99_ms", p95)))
            if rng.random() < .95:
                seconds = rng.uniform(p50, p95) / 1000
            else:
                seconds = rng.uniform(p95, p99 * 1.25) / 1000
        elif category == "publish":
            # No live writes are made during real read-only calibration.
            # Publish duration is an EXPLICIT model assumption, not a sample.
            seconds = state["assumed_publish_ms"] / 1000 * rng.uniform(.6, 1.5)
        else:
            seconds = state["latency"] * rng.uniform(.5, 1.5)
        await asyncio.sleep(min(30.0, seconds * state["profile_scale"]))
        if rng.random() < state["timeout_rate"]:
            # Simulated gateway timeout; never makes any real remote request.
            state["timeouts"] += 1
            await asyncio.sleep(3)
            return JSONResponse({"error": "Synthetic timeout"}, status_code=504)
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
            nonlocal next_image_id
            index = body["datasetId"] - 2000
            results = []
            for item in body["images"]:
                next_image_id += 1
                result = info(ImageApi, id=next_image_id,
                              name=item["title"], datasetId=body["datasetId"], width=256,
                              height=256, size=1600, meta={}, customData={}, tags=[])
                uploaded_images.setdefault(index, []).append(result)
                results.append(result)
            return results
        if method == "jobs.info":
            identifier = body["id"]
            if identifier in created_jobs:
                return created_job(created_jobs[identifier])
            if 3000 <= identifier < 3000 + ANNOTATORS:
                return job(identifier - 3000)
            raise HTTPException(status_code=404, detail="Synthetic job not found")
        if method == "annotations.info":
            identifier = body["imageId"]
            return {"imageId": identifier, "imageName": "synthetic.jpg",
                    "datasetId": 2000 + (identifier - 100000) // FRAMES,
                    "annotation": annotation(identifier), "createdAt": None, "updatedAt": None}
        if method == "workspaces.info":
            return info(WorkspaceApi, id=2001, name="Synthetic workspace", teamId=125980)
        if method == "projects.info":
            return projects_created.get(body["id"], project())
        if method == "projects.list":
            return page([project(), *projects_created.values()], body)
        if method == "projects.add":
            nonlocal next_project_id
            next_project_id += 1
            row = info(ProjectApi, id=next_project_id, name=body["name"],
                       workspaceId=body["workspaceId"], teamId=125980,
                       type="images", itemsCount=0, size=0, customData={})
            projects_created[next_project_id] = row
            return row
        if method == "projects.meta":
            return metadata
        if method == "projects.meta.update":
            metadata.update(body.get("meta", {}))
            return {}
        if method == "datasets.info":
            return datasets_created.get(body["id"], dataset(body["id"] - 2000))
        if method == "datasets.list":
            return page([dataset(index) for index in sorted(remote_datasets)]
                        + list(datasets_created.values()), body)
        if method == "datasets.add":
            nonlocal next_dataset_id
            next_dataset_id += 1
            row = info(DatasetApi, id=next_dataset_id, name=body["name"],
                       projectId=body["projectId"], itemsCount=0,
                       imagesCount=0, size=0, customData={})
            datasets_created[next_dataset_id] = row
            return row
        if method == "images.list":
            source = images(body["datasetId"] - 2000)
            requested = next((
                item.get("value", []) for item in body.get("filter", [])
                if item.get("field") == "id" and item.get("operator") == "in"
            ), None)
            if requested is not None:
                requested = set(requested)
                source = [item for item in source if item["id"] in requested]
            return page(source, body)
        if method == "figures.list":
            # The current Supervisely SDK fetches labeling-job annotations via
            # FigureApi.download -> figures.list, not annotations.info. Returning
            # this exact paging envelope makes validation exercise real JSONs.
            selected = next((
                rule.get("value", []) for rule in body.get("filter", [])
                if rule.get("field") in {"entityId", "imageId"}
            ), [])
            entities = []
            for image_id in selected:
                # For the initial 300 original jobs, a deterministic subset
                # lacks a head keypoint. Frame completeness is assessed by the
                # REAL validator; the simulator never labels jobs incomplete.
                if PHASE == "full" and image_id >= 20_000_000:
                    original_index = (image_id - 20_000_001) // FRAMES
                    first_image = (image_id - 20_000_001) % FRAMES == 0
                    missing = (
                        0 <= original_index < ANNOTATORS
                        and (original_index * 137) % ANNOTATORS
                            < round(INCOMPLETE_RATE * ANNOTATORS)
                        and first_image and image_id not in corrected_images
                    )
                else:
                    original_index = (image_id - 100000) // FRAMES
                    missing = (
                        PHASE == "turnaround" and 0 <= original_index < ANNOTATORS
                        and (original_index * 137) % ANNOTATORS
                            < round(INCOMPLETE_RATE * ANNOTATORS)
                        and (image_id - 100000) % FRAMES == 0
                    )
                objects = annotation(image_id)["objects"]
                if missing:
                    objects = [obj for obj in objects if obj["classTitle"] != "crown"]
                for position, obj in enumerate(objects):
                    figure_id = image_id * 10 + position
                    entities.append({
                        "id": figure_id, "imageId": image_id, "entityId": image_id,
                        "objectId": figure_id,
                        "classId": class_ids[obj["classTitle"]],
                        "projectId": 41, "datasetId": body["datasetId"],
                        "geometryType": obj["geometryType"],
                        "geometry": {"points": obj["points"]},
                        "geometryMeta": {}, "tags": [], "meta": {},
                        "area": "0", "priority": position, "customData": {},
                        "createdAt": None, "updatedAt": None,
                    })
            return page(entities, body)
        if method == "jobs.list":
            index = next((item["value"] - 2000 for item in body.get("filter", [])
                          if item["field"] == "datasetId"), None)
            items = ([job(index)] if index is not None and index < ANNOTATORS
                     and index in submitted_times and PHASE == "turnaround"
                     else [])
            items.extend(created_job(record) for record in created_jobs.values()
                         if index is None or record["dataset_id"] == 2000 + index)
            return page(items, body)
        if method == "users.info":
            return {"teams": [{"id": 125980, "name": "Synthetic team", "role": "annotator", "roleId": 5}]}
        if method == "jobs.add":
            nonlocal next_native_job_id
            users = body.get("userIds") or body.get("usersIds") or []
            if not users:
                index = body["datasetId"] - 2000
                users = [1000 + index % ANNOTATORS]
            # Supervisely LabelingJobApi.create serializes the image subset
            # under meta.entityIds (not at top level). The former fake API
            # accepted the request but reported 30 images for a 1-image
            # correction, falsely flagging correction_subset_not_confirmed.
            selected = (body.get("meta") or {}).get("entityIds")
            if selected is None:
                selected = body.get("imagesIds") or body.get("images_ids") or []
            count = len(selected) if selected else FRAMES
            if "(Incomplete #" in body.get("name", ""):
                corrected_images.update(selected)
            result = []
            for uid in users:
                next_native_job_id += 1
                data = {
                    "id": next_native_job_id, "name": body.get("name", body.get("title", "")),
                    "user_id": uid, "dataset_id": body["datasetId"],
                    "images_count": count,
                    "due": time.monotonic() + (1.5 if PHASE == "full"
                                                       else FRAMES * SECONDS_PER_FRAME),
                }
                created_jobs[data["id"]] = data
                result.append(created_job(data))
                state["published"] += 1
            return result
        state["unhandled"].append({"method": method, "body": body})
        raise HTTPException(status_code=400, detail=f"Unsupported simulator method {method}")

    uvicorn.run(app, host="0.0.0.0", port=443, ssl_keyfile=str(DATA / "tls/key.pem"),
                ssl_certfile=str(DATA / "tls/ca.pem"), access_log=False)


def database_summary():
    from annotatiathon import models as m
    from annotatiathon.db import session_factory
    with session_factory()() as db:
        oldest_pending = db.scalar(select(func.min(m.NativeOperation.created_at)).where(
            m.NativeOperation.status == "pending",
        ))
        oldest_validation = db.scalar(select(func.min(m.NativeOperation.created_at)).where(
            m.NativeOperation.status == "pending",
            m.NativeOperation.kind == "validate_submission",
        ))
        grouped_operations = list(db.execute(
            select(m.NativeOperation.kind, m.NativeOperation.status, func.count())
            .group_by(m.NativeOperation.kind, m.NativeOperation.status),
        ))
        now = m.epoch()
        return {
            "job_states": dict(db.execute(select(m.LabelingJob.status, func.count())
                                            .group_by(m.LabelingJob.status)).all()),
            "operation_states": dict(db.execute(select(m.NativeOperation.status, func.count())
                                                  .group_by(m.NativeOperation.status)).all()),
            "operation_kinds": {f"{kind}:{status}": count
                                for kind, status, count in grouped_operations},
            "oldest_pending_age_seconds": max(0, now - oldest_pending)
                if oldest_pending is not None else 0,
            "oldest_validation_task_age_seconds": max(0, now - oldest_validation)
                if oldest_validation is not None else 0,
            "review_ready_frames": db.scalar(select(func.count())
                .select_from(m.ReviewSample).where(
                    m.ReviewSample.validated_complete.is_(True),
                    m.ReviewSample.completed_at.is_(None),
                )),
            "review_completed_frames": db.scalar(select(func.count())
                .select_from(m.ReviewSample).where(
                    m.ReviewSample.validated_complete.is_(True),
                    m.ReviewSample.completed_at.is_not(None),
                )),
            "operation_errors": list(db.execute(select(m.NativeOperation.error_code,
                                                         func.count()).where(
                m.NativeOperation.error_code.is_not(None)
            ).group_by(m.NativeOperation.error_code)).mappings().all()),
            "reviews": db.scalar(select(func.count()).select_from(m.JobReview)),
            "snapshots": len(list((DATA / "event/dataset/annotations").glob("*/round-*/manifest.json"))),
        }


def full_flow_summary(plan_id, dataset_id):
    """Read-only lifecycle accounting, scoped to this synthetic plan only."""
    from annotatiathon import models as m
    from annotatiathon.db import session_factory

    with session_factory()() as db:
        jobs = list(db.scalars(select(m.LabelingJob).where(
            m.LabelingJob.plan_id == plan_id,
        )))
        source_count = db.scalar(select(func.count(m.MediaAsset.id)).where(
            m.MediaAsset.dataset_id == dataset_id,
        )) or 0
        media_bound = db.scalar(select(func.count(m.NativeSourceMedia.id)).where(
            m.NativeSourceMedia.dataset_id == dataset_id,
        )) or 0
        accepted = db.scalar(select(func.count(m.ReviewSample.id)).join(
            m.LabelingJob, m.LabelingJob.id == m.ReviewSample.job_id,
        ).where(
            m.LabelingJob.plan_id == plan_id,
            m.ReviewSample.validated_complete.is_(True),
        )) or 0
        reviewed = db.scalar(select(func.count(m.ReviewSample.id)).join(
            m.LabelingJob, m.LabelingJob.id == m.ReviewSample.job_id,
        ).where(
            m.LabelingJob.plan_id == plan_id,
            m.ReviewSample.completed_at.is_not(None),
        )) or 0
        operations = list(db.execute(
            select(m.NativeOperation.kind, m.NativeOperation.status,
                   func.count(m.NativeOperation.id))
            .join(m.LabelingJob, m.LabelingJob.id == m.NativeOperation.target_id)
            .where(m.LabelingJob.plan_id == plan_id)
            .group_by(m.NativeOperation.kind, m.NativeOperation.status)
        ))
        work_blocks = list(db.scalars(select(m.WorkBlock).where(
            m.WorkBlock.plan_id == plan_id,
        )))
        return {
            "plan_id": plan_id, "dataset_id": dataset_id,
            "source_frames": source_count, "mirrored_frames": media_bound,
            "source_blocks": len(work_blocks),
            "source_blocks_allocated": sum(block.job_id is not None for block in work_blocks),
            "logical_jobs": len(jobs),
            "jobs_by_status": dict(Counter(job.status for job in jobs)),
            "jobs_with_native_bindings": db.scalar(select(func.count(m.NativeJobBinding.id))
                 .join(m.LabelingJob, m.LabelingJob.id == m.NativeJobBinding.job_id)
                 .where(m.LabelingJob.plan_id == plan_id)) or 0,
            "validated_frames": accepted, "reviewed_frames": reviewed,
            "operation_kinds": {f"{kind}:{status}": count
                                for kind, status, count in operations},
        }


def import_dataset(client, call):
    import_start = time.monotonic()
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
        if PHASE == "full":
            # 360 independent 30-frame videos, each intentionally sampled
            # from sparse source indices. These *real JPEG bytes* traverse
            # the API upload/finalize and Supervisely mirror worker paths.
            from PIL import Image

            for video in range(ANNOTATORS + FULL_FOLLOWUPS):
                for frame in range(FRAMES):
                    index = video * FRAMES + frame
                    buffer = io.BytesIO()
                    Image.new("RGB", (256, 256),
                              (index % 251, (index // 251) % 251,
                               (index // (251 * 251)) % 251)).save(
                                  buffer, "JPEG", quality=65)
                    zipped.writestr(
                        f"Bird/video_{video:04d}/frame_{frame * 5 + 1:08d}.jpg",
                        buffer.getvalue(),
                    )
        else:
            for source in sorted((DATA / "event/dataset/media").glob("*.jpg")):
                zipped.write(source, f"images/bird/{source.name}")
    content = archive.getvalue()
    build_seconds = time.monotonic() - import_start
    transfer_start = time.monotonic()
    response = call(client, "POST", "/api/v1/datasets/uploads", json={
        "name": "Synthetic lifecycle import", "upload_kind": "zip",
        "extraction_policy": "all_frames"})
    response.raise_for_status()
    identifier = response.json()["id"]
    for offset in range(0, len(content), 8 * 1024**2):
        response = call(client, "PUT", f"/api/v1/datasets/uploads/{identifier}/chunk", params={
            "relative_path": "source.zip", "offset": offset, "total": len(content)},
            content=content[offset:offset + 8 * 1024**2],
            headers={"Content-Type": "application/octet-stream"})
        response.raise_for_status()
    transfer_seconds = time.monotonic() - transfer_start
    started = time.monotonic()
    response = call(client, "POST", f"/api/v1/datasets/{identifier}/finalize")
    response.raise_for_status()
    return {"archive_bytes": len(content), "build_zip_seconds": round(build_seconds, 3),
            "transfer_seconds": round(transfer_seconds, 3),
            "finalize_seconds": time.monotonic() - started,
            "dataset": response.json(),
            "uploaded_images": (ANNOTATORS + FULL_FOLLOWUPS) * FRAMES
                if PHASE == "full" else FRAMES}


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
                     "sequence": index // (ANNOTATORS // 2) + 1,
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


def load(seconds, latency, fail_rate, profile_scale=1.0, timeout_rate=0.0,
         assumed_publish_ms=1000):
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

    full_dataset_id = None
    if PHASE == "full" or seconds >= 300:
        with httpx.Client(base_url="https://web", verify=context, trust_env=False,
                          timeout=600) as organiser:
            response = call(organiser, "POST", "/api/v1/auth/login", json={
                "email": "organiser@example.test", "password": PASSWORD})
            response.raise_for_status()
            organiser.headers["X-CSRF-Token"] = response.json()["csrf_token"]
            phases["import"] = import_dataset(organiser, call)
            if PHASE == "full":
                full_dataset_id = phases["import"]["dataset"]["id"]
                # Before publishing, require *real* mirroring of 9,000 JPEGs
                # via the SDK upload worker. No pre-mirrored SQL records.
                mirror_start = time.monotonic()
                while time.monotonic() - mirror_start < 1800:
                    status = call(organiser, "GET",
                                  f"/api/v1/datasets/{full_dataset_id}/mirror-status")
                    status.raise_for_status()
                    state = status.json()
                    if state["ready_for_automatic_jobs"]:
                        phases["mirror"] = {
                            "seconds": round(time.monotonic() - mirror_start, 3),
                            "uploaded_frames": state["uploaded_frames"],
                            "total_frames": state["total_frames"],
                        }
                        break
                    if state["state"] in {"attention", "blocked", "failed"}:
                        raise RuntimeError(
                            f"Synthetic full dataset mirroring failed: {state['error_code']}")
                    time.sleep(3)
                else:
                    raise RuntimeError("Synthetic 9000-frame mirroring timed out")
                plan_start = time.monotonic()
                response = call(organiser, "POST", "/api/v1/labeling/plans", json={
                    "dataset_id": full_dataset_id, "mode": "shared_30_v1",
                })
                response.raise_for_status()
                plan_data = response.json()
                if plan_data["primary_blocks"] != ANNOTATORS + FULL_FOLLOWUPS or (
                        plan_data["unique_frames"] != (ANNOTATORS + FULL_FOLLOWUPS) * FRAMES):
                    raise RuntimeError(
                        "Full-flow plan did not allocate initial and "
                        "follow-up unique 30-frame blocks")
                full_plan_id = plan_data["plan_id"]
                response = call(organiser, "POST",
                                f"/api/v1/labeling/plans/{full_plan_id}/approve")
                response.raise_for_status()
                phases["plan"] = {
                    "seconds": round(time.monotonic() - plan_start, 3),
                    "id": full_plan_id, "blocks": plan_data["primary_blocks"],
                }
    with httpx.Client(verify=context, trust_env=False, timeout=120) as control:
        for attempt in range(60):
            try:
                if control.get("https://supervisely-sim/control/stats").status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(1)
        control.post("https://supervisely-sim/control/start",
                     json={"latency": latency, "fail_rate": fail_rate,
                           "profile_scale": profile_scale, "timeout_rate": timeout_rate,
                           "assumed_publish_ms": assumed_publish_ms,
                           "burst": os.environ.get("BENCHMARK_BURST") == "1"}).raise_for_status()
    deadline = time.monotonic() + seconds
    if PHASE == "full":
        with httpx.Client(base_url="https://web", verify=context,
                          trust_env=False, timeout=120) as organiser:
            response = call(organiser, "POST", "/api/v1/auth/login", json={
                "email": "organiser@example.test", "password": PASSWORD})
            response.raise_for_status()
            organiser.headers["X-CSRF-Token"] = response.json()["csrf_token"]
            launch_start = time.monotonic()
            response = call(organiser, "POST", "/api/v1/native/launch",
                            json={"plan_id": full_plan_id})
            response.raise_for_status()
            phases["launch"] = {"seconds": round(time.monotonic() - launch_start, 3),
                                "status": response.json()["status"]}
    if PHASE == "initial":
        # Release 300 real worker operations at one deterministic instant,
        # after the load/controller clocks have started.
        from annotatiathon import models as m
        from annotatiathon.db import session_factory
        from annotatiathon.native_operations_api import queue_publish
        from annotatiathon.worker_timing import enable, milestone
        enable(str(DATA / "results/worker-timing.jsonl"))
        with session_factory().begin() as db:
            plan = db.get(m.LabelingPlan, "plan")
            if plan.status != "approved":
                raise RuntimeError("Initial publication fixture already launched")
            plan.status = "running"
            jobs = list(db.scalars(select(m.LabelingJob).where(
                m.LabelingJob.plan_id == plan.id,
                m.LabelingJob.status == "queued",
            ).order_by(m.LabelingJob.id)))
            # Only original 300: the follow-up fixtures have job indices >=300.
            jobs = [j for j in jobs if int(j.id.split("-")[-1]) < ANNOTATORS]
            if len(jobs) != ANNOTATORS:
                raise RuntimeError(f"Expected 300 initial jobs, got {len(jobs)}")
            job_ids = [job.id for job in jobs]
            for job in jobs:
                queue_publish(db, job)
        # Keep only primitive IDs across session commit: ORM objects have
        # been expired/detached by SQLAlchemy.
        for job_id in job_ids:
            milestone("publish_queued", job_id=job_id)
        phases["initial_enqueue_count"] = len(job_ids)

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
                if PHASE == "initial":
                    time.sleep(max(0, min(5 - (time.monotonic() - tick), deadline - time.monotonic())))
                    continue
                if PHASE == "full":
                    # Exercise the REAL frame-review endpoints. Monitors never
                    # score incomplete frames; only accepted annotation JSON
                    # enters this fair event-wide queue.
                    for _ in range(2):
                        claimed = call(client, "POST", "/api/v1/review-queue/claim-next")
                        if claimed is None or claimed.status_code != 200:
                            break
                        review = claimed.json().get("review")
                        if not review:
                            break
                        annotation = review["annotation"]
                        labels = annotation.get("objects", annotation.get("labels", []))
                        submitted = call(
                            client, "POST",
                            f"/api/v1/review-queue/{review['review_id']}/complete",
                            json={
                                "claim_token": review["claim_token"],
                                "verdicts": [{"object_index": j, "correct": True}
                                             for j in range(len(labels))],
                                "missing_classes": [], "action": "pause",
                            })
                        if submitted is None or submitted.status_code != 200:
                            raise RuntimeError(
                                "Synthetic monitor could not complete frame review")
                        completed.add(review["review_id"])
                if response is not None and response.status_code == 200:
                    owned = [job for job in response.json()["items"]
                             if int(job["team_id"].split("-")[-1]) % MONITORS == index]
                    # Official frame-level decisions are outside job
                    # turnaround capacity. Do not fake accepting whole jobs.
                    # Monitors never accept official 30-frame jobs. The
                    # isolated benchmark only reads review pages to preserve
                    # representative API traffic.
                    call(client, "GET", "/api/v1/review/datasets/dataset/assets/asset-1/image")
                time.sleep(max(0, min(5 - (time.monotonic() - tick), deadline - time.monotonic())))
        return {"monitor": index, "accepted": len(completed)}

    with concurrent.futures.ThreadPoolExecutor(max_workers=MONITORS) as pool:
        monitors = list(pool.map(monitor, range(MONITORS)))
    rejected_logins = [entry["monitor"] for entry in monitors if entry.get("login_failed")]
    if rejected_logins:
        raise RuntimeError(
            f"Benchmark synthetic monitor logins failed: {rejected_logins}. "
            "Check approved_staff_emails in the isolated benchmark seed.")
    with httpx.Client(verify=context, trust_env=False, timeout=120) as control:
        simulator = control.get("https://supervisely-sim/control/stats").json()
    event_summary = database_summary()
    full_flow = full_flow_summary(full_plan_id, full_dataset_id) if PHASE == "full" else None
    if seconds >= 300 and PHASE != "full":
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
        with concurrent.futures.ThreadPoolExecutor(max_workers=MONITORS) as pool:
            phases["history_reads"] = {"requests": list(pool.map(history_reader, range(MONITORS)))}
        phases["history_reads"]["seconds"] = time.monotonic() - history_started
    latencies = sorted(item["ms"] for item in records)
    result = {
        "duration_seconds": time.monotonic() - started,
        "phase": PHASE, "incomplete_rate": INCOMPLETE_RATE,
        "frames_per_job": FRAMES, "annotators": ANNOTATORS, "monitors_count": MONITORS,
        "seconds_per_frame": SECONDS_PER_FRAME,
        "latency_seconds": latency, "failure_rate": fail_rate,
        "profile_mode": bool(PROFILE_PATH), "profile_scale": profile_scale,
        "timeout_rate": timeout_rate,
        "publish_latency_ms_is_assumed_not_measured": assumed_publish_ms,
        "requests": len(records), "status_counts": dict(Counter(str(item["status"]) for item in records)),
        "p95_ms": latencies[int(len(latencies) * .95)] if latencies else None,
        "p99_ms": latencies[int(len(latencies) * .99)] if latencies else None,
        "transferred_bytes": sum(item["bytes"] for item in records),
        "monitors": monitors, "simulator": simulator, "database": event_summary, "phases": phases,
        "full_dataset_id": full_dataset_id, "full_flow": full_flow,
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
    with concurrent.futures.ThreadPoolExecutor(max_workers=MONITORS) as pool:
        clients = list(pool.map(reader, range(MONITORS)))
    result = {"mode": "history", "frames_per_job": FRAMES,
              "annotators": ANNOTATORS, "monitors_count": MONITORS,
              "duration_seconds": time.monotonic() - started,
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
    parser.add_argument("--profile-scale", type=float, default=1.0)
    parser.add_argument("--timeout-rate", type=float, default=0.0)
    parser.add_argument("--assumed-publish-ms", type=int, default=1000)
    args = parser.parse_args()
    if args.mode == "seed":
        seed(args.frames)
    elif args.mode == "simulate":
        simulate()
    elif args.mode == "load":
        load(args.seconds, args.latency, args.fail_rate,
             args.profile_scale, args.timeout_rate, args.assumed_publish_ms)
    elif args.mode == "profile":
        profile_queries()
    elif args.mode == "history":
        history_load()
    else:
        print(json.dumps(database_summary(), default=str))


if __name__ == "__main__":
    main()
