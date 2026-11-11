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
    teams(client, csrf)
    plan = act(client, csrf, "/labeling/plans", {
        "dataset_id": dataset_id, "mode": "shared_30_v1",
    })
    assert plan.status_code == 201, plan.text
    assert plan.json()["primary_blocks"] == 1
    assert plan.json()["repeat_frames"] == 2
    with maker() as db:
        block = db.scalar(select(WorkBlock).where(WorkBlock.plan_id == plan.json()["plan_id"]))
        assert block.media_ids == [frame.id for frame in frames]
        assert len(set(block.repeat_media_ids)) == 2
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


def test_event_folder_rejects_colliding_ordinals(dataset_ctx):
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
    assert act(client, csrf, f"/datasets/{dataset_id}/finalize").status_code == 422


def test_legacy_folder_discards_all_ordinals_when_any_collide(dataset_ctx):
    client, maker, csrf = dataset_ctx
    dataset = upload_archive(client, csrf, {
        "bird/clip/1.jpg": b"a", "bird/clip/frame_1.jpg": b"b", "bird/clip/2.jpg": b"c",
    })
    assert act(client, csrf, f"/datasets/{dataset['id']}/finalize").status_code == 200
    with maker() as db:
        frames = list(db.scalars(select(MediaAsset).where(MediaAsset.dataset_id == dataset["id"])))
    assert len(frames) == 3
    assert all(frame.frame_index is None for frame in frames)


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


def test_manifest_invalid_time_base_is_a_validation_error():
    from pydantic import ValidationError

    from annotatiathon.frame_import import FrameManifest
    with pytest.raises(ValidationError):
        FrameManifest.model_validate({"version": 1, "source_sha256": "a" * 64,
            "source_frame_count": 1, "all_frames": True, "time_base": "1/0",
            "frames": [{"file": "one.jpg", "ordinal": 1}]})
