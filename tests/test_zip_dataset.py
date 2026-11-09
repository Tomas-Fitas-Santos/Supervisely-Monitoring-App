"""Video-only folder and ZIP inputs must validate to the same atomic video manifest."""
from pathlib import Path
import stat
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import pytest

from monitoring.media_manifest import validate_tree
from monitoring.service import WorkflowError
from monitoring.zip_dataset import extract_zip, inspect_zip

DATASET_FILES = {
    'Bird Study/morphology-A/clip-one/frame_001.png': b'one',
    'Bird Study/morphology-A/clip-one/frame_002.png': b'two',
    'Bird Study/morphology-A/clip-one/frame_010.png': b'ten',
    'Bird Study/morphology-B/clip-two/001.jpg': b'other-one',
    'Bird Study/morphology-B/clip-two/002.jpg': b'other-two',
}


def zip_file(tmp_path, files=None):
    path = tmp_path / 'dataset.zip'
    with ZipFile(path, 'w', ZIP_DEFLATED) as archive:
        for key, value in (DATASET_FILES if files is None else files).items():
            archive.writestr(key, value)
    return path


def test_zip_and_folder_agree_for_video_only_tree(tmp_path):
    direct = validate_tree(list(DATASET_FILES))
    result = inspect_zip(zip_file(tmp_path))
    assert result['manifest'] == direct
    assert direct['image_count'] == 0
    assert direct['video_count'] == 2
    assert direct['frame_count'] == 5
    assert direct['category_count'] == 2
    assert direct['videos'][0]['frames'] == [
        'morphology-A/clip-one/frame_001.png',
        'morphology-A/clip-one/frame_002.png',
        'morphology-A/clip-one/frame_010.png',
    ]


def test_direct_category_folders_without_wrapper(tmp_path):
    entries = {p.removeprefix('Bird Study/'): value for p, value in DATASET_FILES.items()}
    direct = validate_tree(list(entries))
    assert inspect_zip(zip_file(tmp_path, entries))['manifest']['hash'] == direct['hash']


def test_safe_extract_preserves_canonical_source_paths(tmp_path):
    package = zip_file(tmp_path)
    verified = inspect_zip(package)
    manifest, extracted = extract_zip(package, tmp_path/'out',
        verified['archive_sha256'], verified['manifest']['hash'])
    assert manifest['video_count'] == 2
    assert set(extracted) == {p.removeprefix('Bird Study/') for p in DATASET_FILES}
    for key, value in extracted.items():
        assert value.read_bytes() == DATASET_FILES['Bird Study/'+key]


def test_modified_zip_is_rejected_after_preview(tmp_path):
    package = zip_file(tmp_path)
    preview = inspect_zip(package)
    with ZipFile(package, 'a', ZIP_DEFLATED) as archive:
        archive.writestr('Bird Study/morphology-C/clip-new/001.jpg', b'extra')
    with pytest.raises(WorkflowError, match='changed'):
        extract_zip(package, tmp_path/'new', preview['archive_sha256'],
                    preview['manifest']['hash'])


@pytest.mark.parametrize('bad', [
    'Bird Study/../escape.jpg',
    '/Bird Study/morphology-A/clip-one/frame.jpg',
    'Bird Study/morphology-A/clip-one/../../outside.png',
    'Bird Study/morphology-A/clip-one/a.png/../out.png',
])
def test_unsafe_path_rejected(tmp_path, bad):
    entries = dict(DATASET_FILES)
    entries[bad] = b'unsafe'
    with pytest.raises(WorkflowError, match='unsafe|ambiguous'):
        inspect_zip(zip_file(tmp_path, entries))


def test_multiple_wrappers_rejected(tmp_path):
    entries = dict(DATASET_FILES)
    entries['Other Study/morphology-C/clip/001.jpg'] = b'extra'
    with pytest.raises(WorkflowError, match='one dataset root'):
        inspect_zip(zip_file(tmp_path, entries))


def test_rejects_old_images_videos_roots(tmp_path):
    entries = {
        'Bird Study/videos/morphology-A/clip/001.jpg': b'1',
        'Bird Study/videos/morphology-A/clip/002.jpg': b'2',
    }
    with pytest.raises(WorkflowError):
        inspect_zip(zip_file(tmp_path, entries))


def test_symlink_rejected(tmp_path):
    path = tmp_path/'symlink.zip'
    with ZipFile(path,'w', ZIP_DEFLATED) as archive:
        for key, value in DATASET_FILES.items():
            archive.writestr(key,value)
        item=ZipInfo('Bird Study/morphology-A/clip-one/link.jpg')
        item.create_system=3
        item.external_attr=(stat.S_IFLNK|0o777)<<16
        archive.writestr(item,b'../../escape')
    with pytest.raises(WorkflowError,match='symbolic'):
        inspect_zip(path)


def test_unknown_file_and_duplicate_are_rejected(tmp_path):
    entries = dict(DATASET_FILES)
    entries['Bird Study/notes.txt']=b'unexpected'
    with pytest.raises(WorkflowError):
        inspect_zip(zip_file(tmp_path,entries))
    entries=dict(DATASET_FILES)
    entries['Bird Study/morphology-A/clip-one/FRAME_001.PNG']=b'conflict'
    with pytest.raises(WorkflowError,match='duplicate|conflict|ambiguous'):
        inspect_zip(zip_file(tmp_path,entries))
