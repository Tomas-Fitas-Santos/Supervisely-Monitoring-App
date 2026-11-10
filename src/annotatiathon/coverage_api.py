"""Read-only dataset coverage dashboard plus audited observed-movement curation.

Counts always come from imported, unique MediaAsset rows, never assignment
copies or assumed species behaviors. One primary observed movement per frame
is stored in non-overlapping 1-based video frame ranges.
"""
import csv
import io

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .auth import AuthContext, requires
from .datasets_api import dataset_view, get_dataset
from .db import get_db
from .ecomorphology_reference import (
    GROUPS,
    MOVEMENT_LABELS,
    MOVEMENT_TYPES,
    suggested_classification,
)
from .events_api import current_event
from .models import (
    AuditEvent,
    DatasetFolderClassification,
    DatasetMovementSegment,
    MediaAsset,
    epoch,
)

router = APIRouter(prefix="/api/v1/coverage", tags=["dataset coverage review"])
VIEWERS = ("organiser", "monitor", "observer")


class FolderClassificationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_folder: str = Field(min_length=1, max_length=200)
    ecomorphology: str
    species_name: str | None = Field(default=None, max_length=200)


class MovementSegmentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    video_group: str = Field(min_length=1, max_length=500)
    start_frame: int = Field(ge=1, le=250000)
    end_frame: int = Field(ge=1, le=250000)
    movement_type: str
    notes: str = Field(default="", max_length=1000)


def dataset_snapshot(db: Session, dataset_id: str, event_id: str):
    ds = get_dataset(db, event_id, dataset_id)
    records = db.execute(select(
        MediaAsset.morphology, MediaAsset.kind, MediaAsset.video_group,
        func.count(MediaAsset.id),
    ).where(
        MediaAsset.dataset_id == ds.id, MediaAsset.event_id == event_id,
    ).group_by(
        MediaAsset.morphology, MediaAsset.kind, MediaAsset.video_group,
    )).all()
    folders = {
        item.source_folder: item for item in db.scalars(
            select(DatasetFolderClassification).where(
                DatasetFolderClassification.dataset_id == ds.id,
                DatasetFolderClassification.event_id == event_id,
            )
        )
    }
    segments = list(db.scalars(
        select(DatasetMovementSegment).where(
            DatasetMovementSegment.dataset_id == ds.id,
            DatasetMovementSegment.event_id == event_id,
        ).order_by(
            DatasetMovementSegment.video_group,
            DatasetMovementSegment.start_frame,
        )
    ))
    return ds, records, folders, segments


def build_summary(db: Session, dataset_id: str, event_id: str):
    dataset, records, overrides, segments = dataset_snapshot(db, dataset_id, event_id)
    group_counts = {
        group: {"name": group, "frames": 0, "images": 0, "videos": 0,
                "tagged_frames": 0, "species": 0}
        for group in GROUPS
    }
    group_counts["Unclassified"] = {
        "name": "Unclassified", "frames": 0, "images": 0, "videos": 0,
        "tagged_frames": 0, "species": 0,
    }
    folder_counts = {}
    video_groups = {}
    for source, kind, video, count in records:
        if source not in folder_counts:
            proposed_group, proposed_species = suggested_classification(source)
            override = overrides.get(source)
            ecomorphology = (override.ecomorphology if override else proposed_group) or "Unclassified"
            species = (override.species_name if override else proposed_species) or None
            folder_counts[source] = {
                "source_folder": source, "ecomorphology": ecomorphology,
                "species_name": species, "manual": override is not None,
                "frames": 0, "images": 0, "videos": 0,
            }
        folder = folder_counts[source]
        count = int(count)
        if kind == "video_frame" and video:
            folder["frames"] += count
            video_groups[video] = {
                "video_group": video, "source_folder": source,
                "ecomorphology": folder["ecomorphology"],
                "species_name": folder["species_name"],
                "frames": count, "tagged_frames": 0,
                "untagged_frames": count, "segments": [],
            }
        elif kind == "image":
            folder["images"] += count

    movement_counts = {
        key: {"type": key, "label": label, "frames": 0, "videos": set()}
        for key, label in MOVEMENT_TYPES
    }
    for segment in segments:
        video = video_groups.get(segment.video_group)
        if video is None:
            # The source inventory is authoritative; do not count orphaned labels.
            continue
        frames = segment.end_frame - segment.start_frame + 1
        if segment.end_frame > video["frames"]:
            # This should be impossible through our API; treat stale range as
            # unverified instead of inflating the counts.
            continue
        video["segments"].append({
            "id": segment.id, "start_frame": segment.start_frame,
            "end_frame": segment.end_frame, "movement_type": segment.movement_type,
            "notes": segment.notes,
        })
        video["tagged_frames"] += frames
        video["untagged_frames"] -= frames
        movement_counts[segment.movement_type]["frames"] += frames
        movement_counts[segment.movement_type]["videos"].add(segment.video_group)

    movement_rows = [{
        "type": key, "label": row["label"], "frames": row["frames"],
        "videos": len(row["videos"]),
    } for key, row in movement_counts.items()]
    untagged = sum(max(0, v["untagged_frames"]) for v in video_groups.values())
    movement_rows.append({
        "type": "unlabeled", "label": "Not yet movement-labeled",
        "frames": untagged, "videos": sum(v["untagged_frames"] > 0 for v in video_groups.values()),
    })
    for folder in folder_counts.values():
        folder["videos"] = sum(
            v["source_folder"] == folder["source_folder"] for v in video_groups.values()
        )
        group = group_counts[folder["ecomorphology"]]
        group["frames"] += folder["frames"]
        group["images"] += folder["images"]
        group["videos"] += folder["videos"]
        group["species"] += int(bool(folder["species_name"]))
    for video in video_groups.values():
        group_counts[video["ecomorphology"]]["tagged_frames"] += video["tagged_frames"]
    # Cross-tab preserves joint ecomorphology × observed motion provenance.
    # Each video frame contributes to exactly one primary movement or unlabeled.
    movement_matrix = {
        name: {key: 0 for key, _label in MOVEMENT_TYPES}
        | {"unlabeled": 0}
        for name in group_counts
    }
    for video in video_groups.values():
        row = movement_matrix[video["ecomorphology"]]
        row["unlabeled"] += max(0, video["untagged_frames"])
        for segment in video["segments"]:
            row[segment["movement_type"]] += segment["end_frame"] - segment["start_frame"] + 1

    total_video_frames = sum(v["frames"] for v in video_groups.values())
    total_images = sum(f["images"] for f in folder_counts.values())
    return {
        "dataset": dataset_view(dataset),
        "groups": list(group_counts.values()),
        "folders": sorted(folder_counts.values(), key=lambda f: (f["ecomorphology"], f["source_folder"])),
        "movements": movement_rows,
        "movement_matrix": [{
            "ecomorphology": name, "frames": group_counts[name]["frames"],
            "counts": movement_matrix[name],
        } for name in group_counts],
        "video_groups": video_groups,
        "totals": {
            "frames": total_video_frames,
            "images": total_images,
            "annotatable_media": total_video_frames + total_images,
            "videos": len(video_groups),
            "species_folders": len(folder_counts),
            "unclassified_frames": group_counts["Unclassified"]["frames"],
            "unclassified_images": group_counts["Unclassified"]["images"],
            "tagged_frames": total_video_frames - untagged,
            "unlabeled_frames": untagged,
        },
        "reference": {
            "groups": list(GROUPS),
            "movements": [{"type": key, "label": label} for key, label in MOVEMENT_TYPES],
            "frame_order": "1-based, lexicographically sorted frame filenames within each video",
            "taxonomy_source": "Annotatiathon Bird Morphology & Head Motion workbook",
            "movement_policy": "Observed primary movement only; no species-speed inference",
        },
    }


@router.get("/datasets/{dataset_id}")
def coverage_review(
    dataset_id: str,
    ctx: AuthContext = Depends(requires(*VIEWERS)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx)
    data = build_summary(db, dataset_id, event.id)
    data.pop("video_groups")
    return data


@router.get("/datasets/{dataset_id}/videos")
def coverage_videos(
    dataset_id: str,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=30, ge=1, le=100),
    ecomorphology: str | None = Query(default=None, max_length=200),
    search: str = Query(default="", max_length=200),
    status: str = Query(default="all", pattern="^(all|tagged|unlabeled)$"),
    ctx: AuthContext = Depends(requires(*VIEWERS)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx)
    data = build_summary(db, dataset_id, event.id)
    items = list(data["video_groups"].values())
    if ecomorphology and ecomorphology != "all":
        items = [v for v in items if v["ecomorphology"] == ecomorphology]
    if search:
        term = search.casefold()
        items = [
            v for v in items
            if term in v["video_group"].casefold()
            or term in (v["species_name"] or "").casefold()
        ]
    if status == "tagged":
        items = [v for v in items if v["tagged_frames"] > 0]
    if status == "unlabeled":
        items = [v for v in items if v["untagged_frames"] > 0]
    items.sort(key=lambda v: v["video_group"].casefold())
    total = len(items)
    return {
        "page": page, "size": size, "total": total,
        "items": items[(page - 1) * size:page * size],
    }



@router.get("/datasets/{dataset_id}/video-frames")
def inspect_video_frames(
    dataset_id: str,
    video_group: str = Query(min_length=1, max_length=500),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=12, ge=1, le=36),
    ctx: AuthContext = Depends(requires(*VIEWERS)),
    db: Session = Depends(get_db),
):
    """Paginated inspectable frames with stable 1-based lexical frame order."""
    event = current_event(db, ctx)
    dataset = get_dataset(db, event.id, dataset_id)
    criteria = (
        MediaAsset.event_id == event.id,
        MediaAsset.dataset_id == dataset.id,
        MediaAsset.kind == "video_frame",
        MediaAsset.video_group == video_group,
    )
    total = db.scalar(select(func.count(MediaAsset.id)).where(*criteria)) or 0
    if total == 0:
        raise HTTPException(status_code=404, detail="Video not found in this dataset.")
    records = list(db.execute(
        select(MediaAsset.id, MediaAsset.relative_path)
        .where(*criteria).order_by(MediaAsset.relative_path)
        .limit(size).offset((page - 1) * size)
    ))
    return {
        "page": page, "size": size, "total": total,
        "items": [{
            "id": media_id, "relative_path": path,
            "frame": (page - 1) * size + index + 1,
        } for index, (media_id, path) in enumerate(records)],
    }


@router.put("/datasets/{dataset_id}/classification")
def classify_source_folder(
    dataset_id: str, payload: FolderClassificationInput,
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx, writable=True)
    ds = get_dataset(db, event.id, dataset_id, lock=True)
    if ds.status != "ready":
        raise HTTPException(status_code=409, detail="Dataset must be finalized before curation.")
    if payload.ecomorphology not in GROUPS:
        raise HTTPException(status_code=422, detail="Choose one of the 16 ecomorphologies.")
    exists = db.scalar(select(MediaAsset.id).where(
        MediaAsset.event_id == event.id, MediaAsset.dataset_id == ds.id,
        MediaAsset.morphology == payload.source_folder,
    ).limit(1))
    if not exists:
        raise HTTPException(status_code=404, detail="Source folder not found in this dataset.")
    item = db.scalar(select(DatasetFolderClassification).where(
        DatasetFolderClassification.dataset_id == ds.id,
        DatasetFolderClassification.source_folder == payload.source_folder,
    ).with_for_update())
    if item is None:
        item = DatasetFolderClassification(
            event_id=event.id, dataset_id=ds.id, source_folder=payload.source_folder,
            updated_by=ctx.staff.id,
        )
        db.add(item)
    item.ecomorphology = payload.ecomorphology
    item.species_name = (payload.species_name or "").strip() or None
    item.updated_by = ctx.staff.id
    item.updated_at = epoch()
    db.add(AuditEvent(
        staff_id=ctx.staff.id, event_id=event.id,
        action="coverage.classification.update", target_type="media_dataset",
        target_id=ds.id, details={
            "source_folder": payload.source_folder,
            "ecomorphology": item.ecomorphology,
            "species_name": item.species_name,
        },
    ))
    db.commit()
    return {"source_folder": item.source_folder,
            "ecomorphology": item.ecomorphology, "species_name": item.species_name}


@router.post("/datasets/{dataset_id}/movements", status_code=201)
def label_observed_movement(
    dataset_id: str, payload: MovementSegmentInput,
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx, writable=True)
    ds = get_dataset(db, event.id, dataset_id, lock=True)
    if ds.status != "ready":
        raise HTTPException(status_code=409, detail="Dataset must be finalized before curation.")
    if payload.movement_type not in MOVEMENT_LABELS:
        raise HTTPException(status_code=422, detail="Invalid observed movement category.")
    total = db.scalar(select(func.count(MediaAsset.id)).where(
        MediaAsset.dataset_id == ds.id, MediaAsset.event_id == event.id,
        MediaAsset.kind == "video_frame",
        MediaAsset.video_group == payload.video_group,
    )) or 0
    if total == 0:
        raise HTTPException(status_code=404, detail="Video not found in this dataset.")
    if payload.end_frame < payload.start_frame or payload.end_frame > total:
        raise HTTPException(status_code=422, detail="Frame interval exceeds video frame inventory.")
    overlap = db.scalar(select(DatasetMovementSegment.id).where(
        DatasetMovementSegment.dataset_id == ds.id,
        DatasetMovementSegment.video_group == payload.video_group,
        DatasetMovementSegment.start_frame <= payload.end_frame,
        DatasetMovementSegment.end_frame >= payload.start_frame,
    ).limit(1))
    if overlap is not None:
        raise HTTPException(
            status_code=409,
            detail="This frame range overlaps another primary movement. Remove the old range first.",
        )
    segment = DatasetMovementSegment(
        event_id=event.id, dataset_id=ds.id,
        video_group=payload.video_group,
        start_frame=payload.start_frame,
        end_frame=payload.end_frame,
        movement_type=payload.movement_type,
        notes=payload.notes.strip(),
        labeled_by=ctx.staff.id,
    )
    db.add(segment)
    db.flush()
    db.add(AuditEvent(
        staff_id=ctx.staff.id, event_id=event.id,
        action="coverage.movement.label", target_type="dataset_movement_segment",
        target_id=segment.id,
        details={"dataset_id": ds.id, "video_group": payload.video_group,
                 "start_frame": payload.start_frame, "end_frame": payload.end_frame,
                 "movement_type": payload.movement_type},
    ))
    db.commit()
    return {
        "id": segment.id, "video_group": segment.video_group,
        "start_frame": segment.start_frame, "end_frame": segment.end_frame,
        "movement_type": segment.movement_type,
    }


@router.delete("/datasets/{dataset_id}/movements/{segment_id}")
def delete_movement_label(
    dataset_id: str, segment_id: str,
    ctx: AuthContext = Depends(requires("organiser", mutation=True)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx, writable=True)
    get_dataset(db, event.id, dataset_id, lock=True)
    segment = db.scalar(select(DatasetMovementSegment).where(
        DatasetMovementSegment.id == segment_id,
        DatasetMovementSegment.event_id == event.id,
        DatasetMovementSegment.dataset_id == dataset_id,
    ).with_for_update())
    if segment is None:
        raise HTTPException(status_code=404, detail="Movement range not found.")
    db.add(AuditEvent(
        staff_id=ctx.staff.id, event_id=event.id,
        action="coverage.movement.remove", target_type="dataset_movement_segment",
        target_id=segment.id,
        details={"dataset_id": dataset_id, "video_group": segment.video_group,
                 "start_frame": segment.start_frame, "end_frame": segment.end_frame,
                 "movement_type": segment.movement_type},
    ))
    db.delete(segment)
    db.commit()
    return {"deleted": True}


@router.get("/datasets/{dataset_id}/export.csv")
def coverage_csv(
    dataset_id: str,
    ctx: AuthContext = Depends(requires(*VIEWERS)),
    db: Session = Depends(get_db),
):
    event = current_event(db, ctx)
    result = build_summary(db, dataset_id, event.id)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([
        "source_folder", "species_name", "ecomorphology", "video_group",
        "video_frames", "movement_type", "start_frame", "end_frame",
        "counted_frames",
    ])
    for video in sorted(result["video_groups"].values(),
                        key=lambda v: v["video_group"]):
        for segment in video["segments"]:
            writer.writerow([
                video["source_folder"], video["species_name"] or "",
                video["ecomorphology"], video["video_group"], video["frames"],
                segment["movement_type"], segment["start_frame"], segment["end_frame"],
                segment["end_frame"] - segment["start_frame"] + 1,
            ])
        if video["untagged_frames"]:
            writer.writerow([
                video["source_folder"], video["species_name"] or "",
                video["ecomorphology"], video["video_group"], video["frames"],
                "unlabeled", "", "", video["untagged_frames"],
            ])
    return Response(
        content=buffer.getvalue(), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="nightjar-dataset-coverage.csv"',
                 "Cache-Control": "no-store"},
    )
