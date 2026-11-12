"""Every decoded frame keeps a distinct ordinal, even for identical pixels."""
import io
import subprocess

import pytest
import test_dataset_workflows
from PIL import Image
from sqlalchemy import select
from test_dataset_workflows import act, teams, upload_archive

from annotatiathon.models import MediaAsset, WorkBlock

dataset_ctx = test_dataset_workflows.ctx


def test_event_import_decodes_all_thirty_frames_with_timestamps(dataset_ctx, tmp_path):
    client, maker, csrf = dataset_ctx
    video = tmp_path / "one-second.mkv"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "color=c=red:s=64x48:r=30", "-frames:v", "30", "-c:v", "ffv1", str(video)],
                   check=True)
    created = act(client, csrf, "/datasets/uploads", {
        "name": "Full density", "upload_kind": "folder", "extraction_policy": "all_frames"})
    assert created.status_code == 201
    dataset_id = created.json()["id"]
    contents = video.read_bytes()
    response = client.put(f"/api/v1/datasets/uploads/{dataset_id}/chunk", params={
        "relative_path": "bird/clip.mkv", "offset": 0, "total": len(contents)},
        content=contents, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200
    response = act(client, csrf, f"/datasets/{dataset_id}/finalize")
    assert response.status_code == 200, response.text
    assert response.json()["frame_count"] == 30
    assert response.json()["extraction_policy"] == "all_frames"
    with maker() as db:
        frames = list(db.scalars(select(MediaAsset).where(MediaAsset.dataset_id == dataset_id)
                                 .order_by(MediaAsset.frame_index)))
    assert [frame.frame_index for frame in frames] == list(range(1, 31))
    assert len({frame.id for frame in frames}) == 30
    assert len({frame.sha256 for frame in frames}) == 1
    assert all(frame.presentation_timestamp is not None for frame in frames)
    assert all((frame.frame_width, frame.frame_height) == (64, 48) for frame in frames)
    videos = client.get(f"/api/v1/datasets/{dataset_id}/videos").json()["items"]
    assert len(videos) == 1
    assert videos[0]["extraction_policy"] == "all_frames"
    assert len(videos[0]["source_sha256"]) == 64
    assert videos[0]["time_base"]
    assert videos[0]["identity_status"] == "complete"
    readiness = client.get(f"/api/v1/labeling/datasets/{dataset_id}/shared-readiness")
    assert readiness.status_code == 200
    assert readiness.json()["source_ready"] is True
    assert readiness.json()["source_frames"] == 30
    assert readiness.json()["primary_jobs"] == 1
    assert readiness.json()["mirror_ready"] is False
    teams(client, csrf)
    plan = act(client, csrf, "/labeling/plans", {
        "dataset_id": dataset_id, "mode": "shared_30_v1",
    })
    assert plan.status_code == 201, plan.text
    assert plan.json()["primary_blocks"] == 1
    assert plan.json()["repeat_frames"] == 30
    with maker() as db:
        block = db.scalar(select(WorkBlock).where(WorkBlock.plan_id == plan.json()["plan_id"]))
        assert block.media_ids == [frame.id for frame in frames]
        assert block.repeat_media_ids == block.media_ids
        assert set(block.repeat_media_ids).issubset(block.media_ids)
    team = client.get("/api/v1/labeling/manual/options", params={"dataset_id": dataset_id}).json()["teams"][0]
    manual = act(client, csrf, "/labeling/manual/jobs", {
        "dataset_id": dataset_id, "dispatch": "hold",
        "assignments": [{"team_id": team["id"], "assignee_person_id": team["members"][0]["id"],
                         "video_group": videos[0]["video_group"]}],
    })
    assert manual.status_code == 409
    assert act(client, csrf, "/labeling/plans", {"dataset_id": dataset_id}).status_code == 409


def test_supplied_numeric_frames_keep_indices_and_unknown_density(dataset_ctx):
    client, maker, csrf = dataset_ctx
    dataset = upload_archive(client, csrf, {
        "bird/clip/10.jpg": b"frame ten", "bird/clip/2.jpg": b"frame two",
    })
    response = act(client, csrf, f"/datasets/{dataset['id']}/finalize")
    assert response.status_code == 200
    with maker() as db:
        rows = list(db.scalars(select(MediaAsset).where(MediaAsset.dataset_id == dataset['id'])))
    assert {row.relative_path.split("/")[-1]: row.frame_index for row in rows} == {"10.jpg": 10, "2.jpg": 2}
    video = client.get(f"/api/v1/datasets/{dataset['id']}/videos").json()["items"][0]
    assert video["extraction_policy"] == "supplied_unknown"
    assert video["identity_status"] == "gaps"
    assert video["missing_frame_count"] == 8  # Includes missing first frame.


def test_event_folder_accepts_colliding_ordinals_with_natural_order(dataset_ctx):
    client, _, csrf = dataset_ctx
    image = io.BytesIO()
    Image.new("RGB", (32, 24)).save(image, format="JPEG")
    created = act(client, csrf, "/datasets/uploads", {
        "name": "Ambiguous frames", "upload_kind": "folder", "extraction_policy": "all_frames"})
    dataset_id = created.json()["id"]
    for filename in ("bird/clip/1.jpg", "bird/clip/frame_1.jpg"):
        payload = image.getvalue()
        assert client.put(f"/api/v1/datasets/uploads/{dataset_id}/chunk", params={
            "relative_path": filename, "offset": 0, "total": len(payload)},
            content=payload, headers={"X-CSRF-Token": csrf}).status_code == 200
    completed = act(client, csrf, f"/datasets/{dataset_id}/finalize")
    assert completed.status_code == 200, completed.text
    videos = client.get(f"/api/v1/datasets/{dataset_id}/videos").json()["items"]
    assert videos[0]["identity_status"] == "complete"
    assert videos[0]["density"] == "declared_full"


def test_sampled_folder_uses_natural_order_when_ordinals_collide(dataset_ctx):
    client, maker, csrf = dataset_ctx
    dataset = upload_archive(client, csrf, {
        "bird/clip/1.jpg": b"a", "bird/clip/frame_1.jpg": b"b", "bird/clip/2.jpg": b"c",
    })
    assert act(client, csrf, f"/datasets/{dataset['id']}/finalize").status_code == 200
    with maker() as db:
        frames = list(db.scalars(select(MediaAsset).where(MediaAsset.dataset_id == dataset["id"])))
    assert len(frames) == 3
    assert sorted(frame.frame_index for frame in frames) == [1, 2, 3]


def test_full_extraction_obeys_frame_budget(tmp_path):
    from fastapi import HTTPException

    from annotatiathon.frame_extraction import extract_full_video
    video = tmp_path / "many.mkv"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "color=c=red:s=64x48:r=30", "-frames:v", "30", "-c:v", "ffv1", str(video)],
                   check=True)
    with pytest.raises(HTTPException) as error:
        extract_full_video(video, tmp_path / "out", max_frames=10)
    assert error.value.status_code == 413


def test_decoded_ordinals_survive_unavailable_timestamps(tmp_path, monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace

    from annotatiathon.frame_extraction import extract_full_video
    def decode(command, **kwargs):
        target = Path(command[-1]).parent
        for index in range(2):
            (target / f"frame_{index + 1:08d}.jpg").write_bytes(b"jpeg")
            kwargs["stderr"].write(f"[Parsed_showinfo_0] n: {index} pts: NOPTS pts_time:NOPTS s:64x48\n")
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr("annotatiathon.frame_extraction.subprocess.run", decode)
    result = extract_full_video(tmp_path / "source", tmp_path / "frames")
    assert [frame.ordinal for frame in result.frames] == [1, 2]
    assert all(frame.pts is None for frame in result.frames)
    assert result.time_base is None


def test_variable_rate_video_preserves_decoded_frame_timing(tmp_path):
    from fractions import Fraction
    from itertools import pairwise

    from annotatiathon.frame_extraction import extract_full_video
    video = tmp_path / "variable.mkv"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "color=c=red:s=64x48:r=5", "-vf", "setpts=if(lt(N\\,2)\\,N\\,N*3)+15",
                    "-fps_mode", "passthrough", "-frames:v", "5", "-c:v", "ffv1", str(video)], check=True)
    result = extract_full_video(video, tmp_path / "out")
    assert len(result.frames) == 5
    times = [frame.pts for frame in result.frames]
    assert times[0] * Fraction(result.time_base) == 3
    assert len({b - a for a, b in pairwise(times)}) > 1


def test_supplied_manifest_resolves_non_numeric_names(dataset_ctx):
    import hashlib
    import json
    client, _maker, csrf = dataset_ctx
    output = io.BytesIO()
    Image.new("RGB", (32, 24)).save(output, format="JPEG")
    image = output.getvalue()
    manifest = {"version": 1, "source_sha256": "a" * 64, "source_frame_count": 2,
                "all_frames": True, "time_base": "1/30", "frame_rate": "30/1",
                "frames": [{"file": name, "ordinal": index + 1, "pts": index,
                            "width": 32, "height": 24, "sha256": hashlib.sha256(image).hexdigest()}
                           for index, name in enumerate(("z.jpg", "a.jpg"))]}
    dataset = upload_archive(client, csrf, {"bird/clip/z.jpg": image, "bird/clip/a.jpg": image,
                                           "bird/clip/frames.json": json.dumps(manifest).encode()})
    response = act(client, csrf, f"/datasets/{dataset['id']}/finalize")
    assert response.status_code == 200, response.text
    assets = client.get(f"/api/v1/datasets/{dataset['id']}/assets").json()["items"]
    assert [row["relative_path"].split("/")[-1] for row in assets] == ["z.jpg", "a.jpg"]
    video = client.get(f"/api/v1/datasets/{dataset['id']}/videos").json()["items"][0]
    assert video["density"] == "declared_full"
    assert video["extraction_policy"] == "supplied_manifest"



def test_supplied_prefixed_frames_import_without_manifest_in_full_zip(dataset_ctx):
    client, maker, csrf = dataset_ctx
    payload = io.BytesIO()
    Image.new("RGB", (32, 24), "green").save(payload, format="JPEG")
    image = payload.getvalue()
    import zipfile

    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        # The first source ordinal need not be 1 for a selected video segment.
        for i in range(13, 44):
            archive.writestr(
                f"BirdGaze/ColumbaPalumbus/ColumbaPalumba_video_01/"
                f"ColumbaPalumbus_f{i:06d}.jpg", image,
            )
    archive_bytes = stream.getvalue()
    uploaded = act(client, csrf, "/datasets/uploads", {
        "name": "Full exported frames", "upload_kind": "zip",
        "extraction_policy": "all_frames",
    })
    assert uploaded.status_code == 201, uploaded.text
    dataset_id = uploaded.json()["id"]
    sent = client.put(
        f"/api/v1/datasets/uploads/{dataset_id}/chunk",
        params={"relative_path": "source.zip", "offset": 0,
                "total": len(archive_bytes)},
        content=archive_bytes, headers={"X-CSRF-Token": csrf},
    )
    assert sent.status_code == 200, sent.text
    finished = act(client, csrf, f"/datasets/{dataset_id}/finalize")
    assert finished.status_code == 200, finished.text
    assert finished.json()["frame_count"] == 31
    with maker() as db:
        frames = list(db.scalars(select(MediaAsset).where(
            MediaAsset.dataset_id == dataset_id,
        ).order_by(MediaAsset.frame_index)))
        assert [frame.frame_index for frame in frames] == list(range(13, 44))
    video = client.get(f"/api/v1/datasets/{dataset_id}/videos").json()["items"][0]
    assert video["identity_status"] == "complete"
    assert video["density"] == "declared_full"
    assert video["missing_frame_count"] == 0
    teams(client, csrf)
    planned = act(client, csrf, "/labeling/plans", {
        "dataset_id": dataset_id, "mode": "shared_30_v1",
    })
    assert planned.status_code == 201, planned.text
    assert planned.json()["primary_blocks"] == 2
    assert planned.json()["short_final_frames"] == 1


def test_supplied_prefixed_sampled_frames_import_without_manifest(dataset_ctx):
    client, maker, csrf = dataset_ctx
    dataset = upload_archive(client, csrf, {
        f"BirdGaze/ColumbaPalumbus/ColumbaPalumba_video_01_sampled/"
        f"ColumbaPalumbus_f{i:06d}.jpg": b"sampled image bytes"
        for i in (13, 43, 73)
    })
    finished = act(client, csrf, f"/datasets/{dataset['id']}/finalize")
    assert finished.status_code == 200, finished.text
    with maker() as db:
        frames = list(db.scalars(select(MediaAsset).where(
            MediaAsset.dataset_id == dataset["id"],
        ).order_by(MediaAsset.frame_index)))
    assert [frame.frame_index for frame in frames] == [13, 43, 73]
    video = client.get(f"/api/v1/datasets/{dataset['id']}/videos").json()["items"][0]
    assert video["density"] == "sampled"
    assert video["identity_status"] == "gaps"
    assert video["frame_count"] == 3
    readiness = client.get(f"/api/v1/labeling/datasets/{dataset['id']}/shared-readiness")
    assert readiness.status_code == 200
    assert readiness.json()["source_ready"] is True
    assert readiness.json()["issues"] == []
    teams(client, csrf)
    result = act(client, csrf, "/labeling/plans", {
        "dataset_id": dataset["id"], "mode": "shared_30_v1",
    })
    assert result.status_code == 201, result.text
    assert result.json()["primary_blocks"] == 1
    with maker() as db:
        block = db.scalar(select(WorkBlock).where(
            WorkBlock.plan_id == result.json()["plan_id"],
        ))
        assert block.media_ids == [frame.id for frame in frames]


def test_supplied_frame_folder_import_without_numeric_filenames_or_json(dataset_ctx):
    client, maker, csrf = dataset_ctx
    output = io.BytesIO()
    Image.new("RGB", (32, 24), "red").save(output, format="JPEG")
    image = output.getvalue()
    created = act(client, csrf, "/datasets/uploads", {
        "name": "Folder import", "upload_kind": "folder",
        "extraction_policy": "all_frames",
    })
    assert created.status_code == 201, created.text
    dataset_id = created.json()["id"]
    for filename in ("flight_03/scene-z.jpg", "flight_03/scene-a.jpg",
                     "flight_03/scene-b.jpg"):
        path = f"ColumbaPalumbus/{filename}"
        sent = client.put(
            f"/api/v1/datasets/uploads/{dataset_id}/chunk",
            params={"relative_path": path, "offset": 0, "total": len(image)},
            content=image, headers={"X-CSRF-Token": csrf},
        )
        assert sent.status_code == 200, sent.text
    finished = act(client, csrf, f"/datasets/{dataset_id}/finalize")
    assert finished.status_code == 200, finished.text
    with maker() as db:
        ordered = list(db.scalars(select(MediaAsset).where(
            MediaAsset.dataset_id == dataset_id,
        ).order_by(MediaAsset.frame_index)))
    assert [frame.relative_path.split("/")[-1] for frame in ordered] == [
        "scene-a.jpg", "scene-b.jpg", "scene-z.jpg",
    ]
    assert [frame.frame_index for frame in ordered] == [1, 2, 3]
    video = client.get(f"/api/v1/datasets/{dataset_id}/videos").json()["items"][0]
    assert video["identity_status"] == "complete"
    assert video["density"] == "declared_full"
    assert any("natural filename sort" in note for note in video["identity_notes"])


def test_sparse_full_mode_frames_import_but_are_not_full_density(dataset_ctx):
    client, _maker, csrf = dataset_ctx
    output = io.BytesIO()
    Image.new("RGB", (32, 24), "blue").save(output, format="JPEG")
    image = output.getvalue()
    created = act(client, csrf, "/datasets/uploads", {
        "name": "Gapped source", "upload_kind": "folder",
        "extraction_policy": "all_frames",
    })
    dataset_id = created.json()["id"]
    for i in (13, 43):
        path = f"ColumbaPalumbus/clip/ColumbaPalumbus_f{i:06d}.jpg"
        sent = client.put(
            f"/api/v1/datasets/uploads/{dataset_id}/chunk",
            params={"relative_path": path, "offset": 0, "total": len(image)},
            content=image, headers={"X-CSRF-Token": csrf},
        )
        assert sent.status_code == 200, sent.text
    finished = act(client, csrf, f"/datasets/{dataset_id}/finalize")
    assert finished.status_code == 200, finished.text
    video = client.get(f"/api/v1/datasets/{dataset_id}/videos").json()["items"][0]
    assert video["identity_status"] == "gaps"
    assert video["density"] == "unknown"
    assert video["missing_frame_count"] == 29
    readiness = client.get(f"/api/v1/labeling/datasets/{dataset_id}/shared-readiness")
    assert readiness.status_code == 200
    assert readiness.json()["source_ready"] is True
    assert readiness.json()["issues"] == []
    teams(client, csrf)
    planned = act(client, csrf, "/labeling/plans", {
        "dataset_id": dataset_id, "mode": "shared_30_v1",
    })
    assert planned.status_code == 201, planned.text
    assert planned.json()["unique_frames"] == 2


def test_unlinked_frame_in_existing_video_gets_specific_shared_queue_error(dataset_ctx):
    client, maker, csrf = dataset_ctx
    output = io.BytesIO()
    Image.new("RGB", (32, 24), "green").save(output, format="JPEG")
    data = output.getvalue()
    created = act(client, csrf, "/datasets/uploads", {
        "name": "Legacy identity", "upload_kind": "folder",
        "extraction_policy": "all_frames",
    })
    dataset_id = created.json()["id"]
    for number in (1, 2):
        image_path = f"ColumbaPalumbus/clip/frame_{number:06d}.jpg"
        assert client.put(
            f"/api/v1/datasets/uploads/{dataset_id}/chunk",
            params={"relative_path": image_path, "offset": 0, "total": len(data)},
            content=data, headers={"X-CSRF-Token": csrf},
        ).status_code == 200
    assert act(client, csrf, f"/datasets/{dataset_id}/finalize").status_code == 200
    # Emulate a legacy identity gap in an already-finalised source video.
    with maker.begin() as db:
        first = db.scalar(select(MediaAsset).where(MediaAsset.dataset_id == dataset_id))
        first.video_id = None
    teams(client, csrf)
    planned = act(client, csrf, "/labeling/plans", {
        "dataset_id": dataset_id, "mode": "shared_30_v1",
    })
    assert planned.status_code == 422
    assert "not linked to a source video" in planned.json()["detail"]
    assert "video-name/frame_000001.jpg" in planned.json()["detail"]
    readiness = client.get(f"/api/v1/labeling/datasets/{dataset_id}/shared-readiness")
    assert readiness.status_code == 200
    assert readiness.json()["source_ready"] is False
    assert any("no source-video identity" in issue for issue in readiness.json()["issues"])


def test_manifest_invalid_time_base_is_a_validation_error():
    from pydantic import ValidationError

    from annotatiathon.frame_import import FrameManifest
    with pytest.raises(ValidationError):
        FrameManifest.model_validate({"version": 1, "source_sha256": "a" * 64,
            "source_frame_count": 1, "all_frames": True, "time_base": "1/0",
            "frames": [{"file": "one.jpg", "ordinal": 1}]})


def test_thirty_sampled_frames_follow_imported_temporal_order_without_original_gaps(dataset_ctx):
    """30-frame jobs refer to 30 selected images, not 30 adjacent video frames."""
    client, maker, csrf = dataset_ctx
    sample_indices = [7 + 5 * n for n in range(31)]
    # Deliberately reverse the archive insertion order: ordering comes from
    # frame ordinals rather than upload or file-system enumeration.
    dataset = upload_archive(client, csrf, {
        f"BirdGaze/ColumbaPalumbus/sampled_clip/frame_{i:06d}.jpg": b"img"
        for i in reversed(sample_indices)
    })
    finalized = act(client, csrf, f"/datasets/{dataset['id']}/finalize")
    assert finalized.status_code == 200, finalized.text
    with maker() as db:
        frames = list(db.scalars(select(MediaAsset).where(
            MediaAsset.dataset_id == dataset["id"],
        ).order_by(MediaAsset.frame_index)))
    assert [frame.frame_index for frame in frames] == sample_indices
    readiness = client.get(f"/api/v1/labeling/datasets/{dataset['id']}/shared-readiness")
    assert readiness.status_code == 200, readiness.text
    assert readiness.json()["source_ready"] is True
    teams(client, csrf)
    result = act(client, csrf, "/labeling/plans", {
        "dataset_id": dataset["id"], "mode": "shared_30_v1",
    })
    assert result.status_code == 201, result.text
    assert result.json()["primary_blocks"] == 2
    assert result.json()["unique_frames"] == 31
    with maker() as db:
        blocks = list(db.scalars(select(WorkBlock).where(
            WorkBlock.plan_id == result.json()["plan_id"],
        ).order_by(WorkBlock.ordinal)))
    assert [b.frame_count for b in blocks] == [30, 1]
    assert blocks[0].media_ids == [frame.id for frame in frames[:30]]
    assert blocks[1].media_ids == [frames[-1].id]
