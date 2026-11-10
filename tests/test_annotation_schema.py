"""Supervisely bird annotation classes: exact names, geometry, color and safety."""
import pytest

from annotatiathon.annotation_schema import (
    ANNOTATION_CLASSES,
    AnnotationSchemaError,
    annotation_class_view,
    ensure_project_annotation_classes,
    merge_annotation_meta,
)


def test_canonical_bird_schema_has_one_rectangle_and_eight_points():
    rows = annotation_class_view()
    assert len(rows) == 9
    assert len({item["name"] for item in rows}) == 9
    assert rows[0]["name"] == "bird"
    assert rows[0]["shape"] == "rectangle"
    assert [item["name"] for item in rows[1:]] == [
        "left_eye", "left_eye_occ", "right_eye", "right_eye_occ",
        "beak", "beak_occ", "crown", "crown_occ",
    ]
    assert all(item["shape"] == "point" for item in rows[1:])
    assert all(item["color"].startswith("#") and len(item["color"]) == 7 for item in rows)
    # Distinct anatomical families, with visible/occluded paired shades.
    assert len({item["color"] for item in rows}) == 9
    for visible, hidden in ((1, 2), (3, 4), (5, 6), (7, 8)):
        assert rows[visible]["color"] != rows[hidden]["color"]


def test_merge_preserves_existing_classes_tags_ids_and_project_settings():
    current = {
        "projectType": "images",
        "classes": [
            {"title": "unrelated", "shape": "polygon", "color": "#123456", "id": 88},
            {"title": "bird", "shape": "rectangle", "color": "#000000",
             "id": 100, "hotkey": "b"},
        ],
        "tags": [{"name": "source", "value_type": "any_string"}],
        "projectSettings": {"multiView": {"enabled": False}},
    }
    result, changed = merge_annotation_meta(current)
    assert changed
    assert len(result["classes"]) == 10
    classes = {item["title"]: item for item in result["classes"]}
    assert classes["unrelated"]["id"] == 88
    assert classes["bird"]["id"] == 100
    assert classes["bird"]["hotkey"] == "b"
    assert classes["bird"]["color"] == "#FFD600"
    assert result["tags"] == current["tags"]
    assert result["projectSettings"] == current["projectSettings"]
    assert current["classes"][1]["color"] == "#000000"
    assert {name: row["shape"] for name, row in classes.items()
            if name != "unrelated"} == {
                name: shape for name, shape, _color, _description in ANNOTATION_CLASSES
            }
    repeat, changed_again = merge_annotation_meta(result)
    assert not changed_again
    assert repeat == result


def test_conflicting_project_class_shape_is_rejected_without_mutating_meta():
    original = {
        "projectType": "images", "classes": [
            {"title": "beak", "shape": "rectangle", "color": "#111111"}
        ], "tags": [],
    }
    with pytest.raises(AnnotationSchemaError, match="annotation_class_geometry_conflict"):
        merge_annotation_meta(original)
    assert original["classes"][0]["shape"] == "rectangle"


def test_project_api_get_and_update_are_idempotent():
    class Project:
        def __init__(self):
            self.meta = {"classes": [], "tags": [], "projectType": "images"}
            self.calls = 0

        def get_meta(self, project_id):
            assert project_id == 42
            return self.meta

        def update_meta(self, project_id, meta):
            assert project_id == 42
            self.calls += 1
            self.meta = meta

    class API:
        def __init__(self):
            self.project = Project()

    api = API()
    assert ensure_project_annotation_classes(api, 42)
    assert api.project.calls == 1
    assert not ensure_project_annotation_classes(api, 42)
    assert api.project.calls == 1
