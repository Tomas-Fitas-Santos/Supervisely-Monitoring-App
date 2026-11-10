"""Canonical Supervisely class schema for Nightjar bird-frame annotations.

The annotation schema is *project* metadata, shared by all datasets and
labeling jobs in the same Nightjar-owned Supervisely image project.

Visible landmarks use vivid, saturated hues. Occluded counterparts use
lighter, muted shades of the same hue; anatomy remains identifiable by
color family even when the class name is hidden by the labeling UI.
"""
from copy import deepcopy

# (Exact Supervisely class title, geometry, RGB hex, on-screen meaning)
ANNOTATION_CLASSES: tuple[tuple[str, str, str, str], ...] = (
    ("bird", "rectangle", "#FFD600", "Bird bounding box"),
    ("left_eye", "point", "#00C853", "Left eye, visible"),
    ("left_eye_occ", "point", "#91D7A9", "Left eye, occluded"),
    ("right_eye", "point", "#00B8D4", "Right eye, visible"),
    ("right_eye_occ", "point", "#8BCED9", "Right eye, occluded"),
    ("beak", "point", "#FF6D00", "Beak, visible"),
    ("beak_occ", "point", "#F5B282", "Beak, occluded"),
    ("crown", "point", "#AA00FF", "Crown, visible"),
    ("crown_occ", "point", "#D3A1F0", "Crown, occluded"),
)


class AnnotationSchemaError(ValueError):
    """A remote project class cannot be safely reconciled."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def annotation_class_view() -> list[dict]:
    """Read-only class descriptions for the Nightjar allocation screen."""
    return [
        {"name": name, "shape": shape, "color": color, "description": description}
        for name, shape, color, description in ANNOTATION_CLASSES
    ]


def merge_annotation_meta(current: dict) -> tuple[dict, bool]:
    """Add/repair required class definitions without deleting other project meta.

    Existing class identities (IDs and class-specific options), tags and
    project settings must survive. If an existing name uses an incompatible
    geometry, abort before uploading any more images or creating native jobs.
    """
    if not isinstance(current, dict):
        raise AnnotationSchemaError("invalid_project_meta")
    existing_classes = current.get("classes", [])
    if not isinstance(existing_classes, list):
        raise AnnotationSchemaError("invalid_project_classes")
    if current.get("projectType", "images") not in ("images", None):
        raise AnnotationSchemaError("project_must_be_images")
    merged = deepcopy(current)
    classes = merged.setdefault("classes", [])
    by_name: dict[str, dict] = {}
    for cls in classes:
        if not isinstance(cls, dict):
            raise AnnotationSchemaError("invalid_project_classes")
        name = cls.get("title")
        if not isinstance(name, str) or not name or name in by_name:
            raise AnnotationSchemaError("duplicate_or_invalid_project_class")
        by_name[name] = cls
    changed = False
    for name, shape, color, _meaning in ANNOTATION_CLASSES:
        if name not in by_name:
            classes.append({
                "title": name, "shape": shape,
                "color": color, "geometry_config": {},
            })
            changed = True
            continue
        existing = by_name[name]
        if existing.get("shape") != shape:
            raise AnnotationSchemaError("annotation_class_geometry_conflict")
        if existing.get("color", "").upper() != color:
            existing["color"] = color
            changed = True
    return merged, changed


def ensure_project_annotation_classes(api, project_id: int) -> bool:
    """Use the documented Supervisely ProjectApi get_meta/update_meta contract.

    Returns True only when the remote project's metadata needs a write.
    This operation is safe to retry after an uncertain worker interruption.
    """
    current = api.project.get_meta(project_id)
    desired, changed = merge_annotation_meta(current)
    if changed:
        api.project.update_meta(project_id, desired)
    return changed
