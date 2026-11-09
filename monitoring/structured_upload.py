"""One structured dataset importer for browser folder and ZIP uploads.

Both inputs yield identical category/video/frame manifests and native videos. An explicit preview precedes remote mutation.
"""
from pathlib import Path, PurePosixPath
import shutil
import tempfile

from sqlalchemy import update

from .db import EventConfig, MediaSequence, SourceOrigin, Upload, UploadedPath
from .event_settings import single_team
from .media_manifest import encode_video, validate_tree
from .service import WorkflowError
from .setup import pilot_meta
from .zip_dataset import extract_zip, inspect_zip


def _staged(setup, actor, upload_ids):
    if (not isinstance(upload_ids, list) or not upload_ids or
            len(set(upload_ids)) != len(upload_ids)):
        raise WorkflowError('Select unique staged dataset files.')
    with setup.sessions() as session:
        uploads = [session.get(Upload, uid) for uid in upload_ids]
        if any(not item or item.actor_id != actor or item.offset != item.size
               or item.consumed for item in uploads):
            raise WorkflowError(
                'One or more uploaded files are incomplete, consumed or belong to another user.')
        files = [(item, (session.get(UploadedPath, item.id).path
                        if session.get(UploadedPath, item.id) else item.name))
                 for item in uploads]
    return files


def _rebuild(setup, item, tmp):
    destination = Path(tmp) / (item.id + Path(item.name).suffix.lower())
    position = 0
    with destination.open('xb') as output:
        while position < item.size:
            part = setup.upload_dir / item.id / str(position)
            if not part.is_file():
                raise WorkflowError('A staged dataset chunk is missing.')
            content = part.read_bytes()
            if not content or position + len(content) > item.size:
                raise WorkflowError('A staged dataset chunk is corrupt.')
            output.write(content)
            position += len(content)
    return destination


def _canonical(path):
    parts = PurePosixPath(str(path).replace('\\', '/')).parts
    if not parts:
        raise WorkflowError('Dataset file does not have a relative path.')
    if len(parts) == 4:
        parts = parts[1:]
    if len(parts) != 3:
        raise WorkflowError('Expected category/video/frame path.')
    return '/'.join(parts)


def _snapshot(setup, actor, ids, mode, staging_directory, *, extract=False,
              expected_hash=None, expected_archive_sha=None):
    records = _staged(setup, actor, ids)
    if mode == 'zip':
        if len(records) != 1 or Path(records[0][1]).suffix.casefold() != '.zip':
            raise WorkflowError('Select exactly one .zip dataset archive.')
        archive_file = _rebuild(setup, records[0][0], staging_directory)
        if extract:
            if not expected_hash or not expected_archive_sha:
                raise WorkflowError('Preview the archive before confirming upload.')
            manifest, paths = extract_zip(
                archive_file, Path(staging_directory) / 'archive-extracted',
                expected_archive_sha, expected_hash)
            return manifest, paths, {
                'archive_sha256': expected_archive_sha,
                'unpacked_bytes': sum(p.stat().st_size for p in paths.values()),
            }
        checked = inspect_zip(archive_file)
        return checked['manifest'], {}, {
            'archive_sha256': checked['archive_sha256'],
            'unpacked_bytes': checked['unpacked_bytes'],
        }
    if mode != 'folder':
        raise WorkflowError('Choose Folder or ZIP for dataset upload.')
    if any(Path(path).suffix.casefold() == '.zip' for _, path in records):
        raise WorkflowError('Folder mode cannot include ZIP files.')
    manifest = validate_tree([path for _, path in records])
    if extract:
        if expected_hash != manifest['hash']:
            raise WorkflowError('Selected folder changed since preview.')
        paths = {}
        for item, path in records:
            canonical = _canonical(path)
            if canonical in paths:
                raise WorkflowError('Two selected files share the same relative path.')
            paths[canonical] = _rebuild(setup, item, staging_directory)
        return manifest, paths, {'archive_sha256': None, 'unpacked_bytes':
                                  sum(path.stat().st_size for path in paths.values())}
    return manifest, {}, {'archive_sha256': None, 'unpacked_bytes':
                          sum(item.size for item, _ in records)}


def preview_staged(setup, actor, ids, mode):
    with tempfile.TemporaryDirectory(prefix='nightjar-inspect-') as tmp:
        manifest, _, extra = _snapshot(setup, actor, ids, mode, tmp)
    return {
        'hash': manifest['hash'],
        'images': manifest['image_count'], 'videos': manifest['video_count'],
        'frames': manifest['frame_count'],
        'categories': manifest['category_count'],
        'video_examples': [
            {'name': row['title'], 'category': row['category'],
             'frames': row['frame_count']}
            for row in manifest['videos'][:12]],
        'archive_sha256': extra['archive_sha256'],
        'unpacked_bytes': extra['unpacked_bytes'],
        'mode': mode,
    }


def _publish(setup, gateway, actor, operation_id, staged_ids, name, fps, meta,
             manifest, paths, workspace_id, team_id, temp_dir, mode):
    if not isinstance(name, str) or not name.strip() or len(name) > 180:
        raise WorkflowError('Choose a name for the Supervisely source dataset.')
    try:
        fps = int(fps)
    except (TypeError, ValueError):
        raise WorkflowError('Frame rate must be between 1 and 60.') from None
    if not 1 <= fps <= 60:
        raise WorkflowError('Frame rate must be between 1 and 60.')
    remote = [(row['path'], paths[row['path']]) for row in manifest['images']]
    video_mapping = {}
    for i, video in enumerate(manifest['videos']):
        generated = f"{video['category']}/{video['title']}.mp4"
        output = Path(temp_dir) / f'encoded-video-{i:05d}.mp4'
        encode_video([paths[frame] for frame in video['frames']], output, fps=fps)
        remote.append((generated, output))
        video_mapping[generated] = video

    import supervisely as sly
    try:
        meta = sly.ProjectMeta.from_json(meta or pilot_meta()).to_json()
    except Exception:
        raise WorkflowError('Invalid Supervisely annotation schema.') from None
    try:
        native = gateway.api.workspace.get_info_by_id(workspace_id)
    except Exception:
        raise WorkflowError('Configured destination workspace is inaccessible.') from None
    if not native or native.team_id != team_id:
        raise WorkflowError('Configured workspace does not belong to the shared event team.')

    with setup.operation(actor, operation_id, 'structured_dataset_upload', {
            'mode':mode, 'tree_hash':manifest['hash'],
            'image_count':manifest['image_count'],
            'video_count':manifest['video_count'],
            'frame_count':manifest['frame_count'],
            'fps':fps}) as checkpoint:
        with setup.sessions.begin() as session:
            for uid in staged_ids:
                updated = session.execute(update(Upload).where(
                    Upload.id == uid, Upload.actor_id == actor,
                    Upload.consumed.is_(False)).values(consumed=True))
                if updated.rowcount != 1:
                    raise WorkflowError('A staged dataset file was already used.')
        uploaded = gateway.upload(team_id, workspace_id, name.strip(),
                                  'mixed', remote, meta, checkpoint)
        source_map = {r['relative_path']: r for r in uploaded['sources']}
        with setup.sessions.begin() as session:
            for source in uploaded['sources']:
                session.merge(SourceOrigin(**source))
            for generated, video in video_mapping.items():
                source = source_map.get(generated)
                if not source:
                    raise WorkflowError('Native video upload did not return a source ID.')
                session.merge(MediaSequence(
                    source_id=source['source_id'],
                    source_folder=video['source_folder'],
                    category=video['category'], title=video['title'],
                    frame_paths=video['frames'],
                    frame_count=video['frame_count'], fps=fps,
                ))
        checkpoint({'committed_source_assets':len(uploaded['sources'])})
    for uid in staged_ids:
        shutil.rmtree(setup.upload_dir / uid, ignore_errors=True)
    return {**uploaded, 'manifest': {
        'image_count':manifest['image_count'],
        'video_count':manifest['video_count'],
        'frame_count':manifest['frame_count'],
        'tree_hash':manifest['hash'],
        'mode':mode,
    }}


def upload_structured(setup, gateway, actor, operation_id, upload_ids,
                      mode, name, fps, meta, expected_tree_hash,
                      expected_archive_sha256=None):
    with setup.sessions() as session:
        config = session.get(EventConfig, 1)
        if not single_team(config) or not config.source_workspace_id:
            raise WorkflowError('Configure the existing team and workspace in Settings.')
        team_id = config.monitoring_team_id
        workspace_id = config.source_workspace_id
    if not isinstance(expected_tree_hash, str) or len(expected_tree_hash) != 64:
        raise WorkflowError('Inspect the dataset before confirming upload.')
    # Check path and archive hash again before any remote upload.
    with tempfile.TemporaryDirectory(prefix='nightjar-dataset-') as tmp:
        manifest, paths, _ = _snapshot(
            setup, actor, upload_ids, mode, tmp,
            extract=True, expected_hash=expected_tree_hash,
            expected_archive_sha=expected_archive_sha256
        )
        return _publish(
            setup, gateway, actor, operation_id, upload_ids, name, fps, meta,
            manifest, paths, workspace_id, team_id, tmp, mode
        )
