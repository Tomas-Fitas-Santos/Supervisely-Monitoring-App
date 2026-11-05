"""A ZIP and an uploaded folder must produce exactly the same event manifest."""
from pathlib import Path
import stat
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import pytest

from monitoring.media_manifest import validate_tree
from monitoring.service import WorkflowError
from monitoring.zip_dataset import extract_zip, inspect_zip


DATASET_FILES = {
    'Bird Study/images/morphology-A/bird-01.jpg': b'image-one',
    'Bird Study/images/morphology-B/bird-02.png': b'image-two',
    'Bird Study/videos/morphology-A/clip-one/frame_001.png': b'frame-one',
    'Bird Study/videos/morphology-A/clip-one/frame_002.png': b'frame-two',
    'Bird Study/videos/morphology-A/clip-one/frame_010.png': b'frame-ten',
    'Bird Study/videos/morphology-B/clip-two/001.jpg': b'other-one',
    'Bird Study/videos/morphology-B/clip-two/002.jpg': b'other-two',
}


def make_zip(tmp_path, files=None):
    file = tmp_path / 'dataset.zip'
    with ZipFile(file, 'w', ZIP_DEFLATED) as archive:
        for path, data in (files or DATASET_FILES).items():
            archive.writestr(path, data)
    return file


def test_named_dataset_zip_and_selected_folder_have_same_manifest(tmp_path):
    file = make_zip(tmp_path)
    folder = validate_tree(list(DATASET_FILES), require_both=True)
    zipped = inspect_zip(file)
    assert zipped['manifest']['hash'] == folder['hash']
    assert zipped['manifest']['image_count'] == 2
    assert zipped['manifest']['video_count'] == 2
    assert zipped['manifest']['frame_count'] == 5
    assert zipped['manifest']['category_count'] == 2
    first_video = zipped['manifest']['videos'][0]
    assert first_video['frames'] == [
        'videos/morphology-A/clip-one/frame_001.png',
        'videos/morphology-A/clip-one/frame_002.png',
        'videos/morphology-A/clip-one/frame_010.png',
    ]


def test_zip_may_contain_direct_images_and_videos_roots(tmp_path):
    direct = {path.removeprefix('Bird Study/'): data
              for path, data in DATASET_FILES.items()}
    zip_file = make_zip(tmp_path, direct)
    assert inspect_zip(zip_file)['manifest']['hash'] == validate_tree(
        list(DATASET_FILES))['hash']


def test_zip_extracts_to_generated_safe_paths_after_preview(tmp_path):
    archive = make_zip(tmp_path)
    preview = inspect_zip(archive)
    manifest, outputs = extract_zip(
        archive, tmp_path / 'extracted',
        preview['archive_sha256'], preview['manifest']['hash'],
    )
    assert len(outputs) == len(DATASET_FILES)
    assert set(outputs) == {
        path.removeprefix('Bird Study/') for path in DATASET_FILES
    }
    assert (tmp_path / 'extracted').exists()
    assert all((tmp_path / 'extracted') in path.parents for path in outputs.values())
    for source, output in outputs.items():
        assert output.read_bytes() == DATASET_FILES['Bird Study/' + source]


def test_archive_modified_after_preview_is_rejected(tmp_path):
    archive = make_zip(tmp_path)
    preview = inspect_zip(archive)
    with ZipFile(archive, 'a', ZIP_DEFLATED) as append:
        append.writestr('Bird Study/images/new/bird.png', b'extra')
    with pytest.raises(WorkflowError, match='changed'):
        extract_zip(archive, tmp_path / 'target',
                    preview['archive_sha256'], preview['manifest']['hash'])


@pytest.mark.parametrize('bad_path', [
    'Bird Study/../outside.jpg',
    'Bird Study/images/morphology-A/../../outside.png',
    '/Bird Study/images/morphology-A/bird.jpg',
    'Bird Study/videos/morphology-A/clip-one/frame.png/../out.png',
])
def test_zip_rejects_unsafe_paths(tmp_path, bad_path):
    rows = dict(DATASET_FILES)
    rows[bad_path] = b'not-safe'
    with pytest.raises(WorkflowError, match='unsafe|ambiguous'):
        inspect_zip(make_zip(tmp_path, rows))


def test_zip_rejects_multiple_dataset_roots(tmp_path):
    rows = dict(DATASET_FILES)
    rows['Other Study/images/morphology-C/extra.jpg'] = b'another-root'
    with pytest.raises(WorkflowError, match='one dataset|root folder'):
        inspect_zip(make_zip(tmp_path, rows))


def test_zip_rejects_missing_videos_or_wrong_depth(tmp_path):
    rows = {k:v for k,v in DATASET_FILES.items() if '/images/' in k}
    with pytest.raises(WorkflowError, match='both images'):
        inspect_zip(make_zip(tmp_path, rows))
    rows = dict(DATASET_FILES)
    rows['Bird Study/videos/clip-no-category/frame.png'] = b'frame'
    with pytest.raises(WorkflowError, match='Use images'):
        inspect_zip(make_zip(tmp_path, rows))


def test_zip_rejects_symlink_entry(tmp_path):
    archive_file = tmp_path / 'dataset.zip'
    with ZipFile(archive_file, 'w', ZIP_DEFLATED) as archive:
        for filename, content in DATASET_FILES.items():
            archive.writestr(filename, content)
        sym = ZipInfo('Bird Study/images/morphology-A/link.jpg')
        sym.create_system = 3
        sym.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(sym, b'../../outside.jpg')
    with pytest.raises(WorkflowError, match='symbolic'):
        inspect_zip(archive_file)


def test_zip_ignores_mac_finder_metadata_but_not_unknown_files(tmp_path):
    rows = dict(DATASET_FILES)
    rows['__MACOSX/._bird.jpg'] = b'finder'
    rows['Bird Study/.DS_Store'] = b'finder'
    assert inspect_zip(make_zip(tmp_path, rows))['manifest']['video_count'] == 2
    rows['Bird Study/README.txt'] = b'not-a-media-asset'
    with pytest.raises(WorkflowError):
        inspect_zip(make_zip(tmp_path, rows))
