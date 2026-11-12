"""Submission-time *structural* validation for one bird per image.

This is not a correctness judge: monitors assess the position and class of
every submitted object. A complete frame must contain exactly one bird
rectangle and one point from each of the four visible/occluded landmark pairs.
"""
from collections import Counter

LANDMARKS = ("left_eye", "right_eye", "beak", "crown")
ALLOWED = {"bird", *(name for root in LANDMARKS for name in (root, root + "_occ"))}


def frame_completion_errors(annotation: dict) -> list[str]:
    if not isinstance(annotation, dict):
        return ["invalid_annotation"]
    objects = annotation.get("objects", annotation.get("labels"))
    if not isinstance(objects, list):
        return ["missing_objects"]
    counts: Counter[str] = Counter()
    errors: list[str] = []
    for obj in objects:
        if not isinstance(obj, dict):
            errors.append("invalid_object")
            continue
        cls = obj.get("classTitle")
        geometry = obj.get("geometryType")
        if cls not in ALLOWED:
            errors.append("unexpected_class")
            continue
        if geometry != ("rectangle" if cls == "bird" else "point"):
            errors.append("invalid_geometry")
            continue
        counts[cls.removesuffix("_occ")] += 1
    for cls in ("bird", *LANDMARKS):
        if counts[cls] != 1:
            errors.append(f"{cls}_count_{counts[cls]}")
    # No unrelated objects can be allowed even when all five are present.
    return errors


def frame_is_complete(annotation: dict) -> bool:
    return not frame_completion_errors(annotation)
