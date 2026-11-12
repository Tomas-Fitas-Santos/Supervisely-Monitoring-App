"""Structural per-frame validation has no dependence on native job counts."""
import pytest

from annotatiathon.frame_completion import frame_completion_errors, frame_is_complete


def complete_frame():
    return {"objects": [
        {"classTitle": "bird", "geometryType": "rectangle"},
        {"classTitle": "left_eye", "geometryType": "point"},
        {"classTitle": "right_eye_occ", "geometryType": "point"},
        {"classTitle": "beak", "geometryType": "point"},
        {"classTitle": "crown_occ", "geometryType": "point"},
    ]}


def test_accepts_mixed_visible_and_occluded_anatomy():
    annotation = complete_frame()
    assert frame_completion_errors(annotation) == []
    assert frame_is_complete(annotation)
    annotation["objects"][1]["classTitle"] = "left_eye_occ"
    annotation["objects"][2]["classTitle"] = "right_eye"
    assert frame_is_complete(annotation)


@pytest.mark.parametrize(("change", "expected"), [
    ("missing_bird", "bird_count_0"),
    ("duplicate_bird", "bird_count_2"),
    ("missing_beak", "beak_count_0"),
    ("visible_and_occluded_left", "left_eye_count_2"),
    ("wrong_geometry", "invalid_geometry"),
    ("unexpected_class", "unexpected_class"),
])
def test_rejects_structurally_incomplete_frames(change, expected):
    annotation = complete_frame()
    objects = annotation["objects"]
    if change == "missing_bird":
        objects.pop(0)
    elif change == "duplicate_bird":
        objects.append(dict(objects[0]))
    elif change == "missing_beak":
        objects.pop(3)
    elif change == "visible_and_occluded_left":
        objects.append({"classTitle": "left_eye_occ", "geometryType": "point"})
    elif change == "wrong_geometry":
        objects[1]["geometryType"] = "rectangle"
    elif change == "unexpected_class":
        objects.append({"classTitle": "another", "geometryType": "point"})
    assert expected in frame_completion_errors(annotation)
    assert not frame_is_complete(annotation)


def test_missing_objects_array_is_incomplete():
    assert frame_completion_errors({}) == ["missing_objects"]
    assert frame_completion_errors({"objects": []}) == [
        "bird_count_0", "left_eye_count_0", "right_eye_count_0",
        "beak_count_0", "crown_count_0",
    ]
