"""Import selected images and frame-sequence videos into the configured workspace.

Uses the same staging and native upload path as ordinary source uploads.
Every frame sequence is encoded as a *complete* MP4 before remote writes.
"""
import hashlib
from pathlib import Path, PurePosixPath
import tempfile

from sqlalchemy import select, update

from .db import EventConfig, MediaSequence, SourceOrigin, Upload, UploadedPath
from .event_settings import single_team
from .media_manifest import encode_video, validate_tree
from .service import WorkflowError
from .setup import pilot_meta, positive


def _canonical_path(value):
    raw = value.replace('\\', '/')
    parts = PurePosixPath(raw).parts
    if parts[0].casefold() not in ('images', 'videos'):
        parts = parts[1:]
    return '/'.join((parts[0].casefold(), *parts[1:]))


def upload_frame_folder(setup, gateway, actor, operation_id, upload_ids, name, fps=12, meta=None, expected_tree_hash=None):
    """Validates, pre-encodes and uploads with an explicit remote operation log."""
    if not upload_ids or len(upload_ids) != len(set(upload_ids)):
        raise WorkflowError('Select a unique set of staged dataset files.')
    if not isinstance(name, str) or not name.strip() or len(name) > 180:
        raise WorkflowError('Enter a source project name.')
    with setup.sessions() as session:
        config = session.get(EventConfig, 1)
        if not single_team(config) or not config.source_workspace_id:
            raise WorkflowError('Configure your existing team and workspace in Settings.')
        workspace_id, team_id = config.source_workspace_id, config.monitoring_team_id
        uploads = [session.get(Upload, key) for key in upload_ids]
        if any(not item or item.actor_id != actor or item.offset != item.size or item.consumed
               for item in uploads):
            raise WorkflowError('The dataset contains unfinished or already-used staged uploads.')
        paths = {
            item.id: ((session.get(UploadedPath, item.id).path
                       if session.get(UploadedPath, item.id) else item.name))
            for item in uploads
        }
    manifest = validate_tree(list(paths.values()), require_both=True)
    if not isinstance(expected_tree_hash, str) or expected_tree_hash != manifest['hash']:
        raise WorkflowError('The selected folder changed after preview. Review the structure again.')
    records = {_canonical_path(path): key for key, path in paths.items()}
    if len(records) != len(upload_ids):
        raise WorkflowError('Two selected files have the same dataset-relative path.')

    # Do not create a remote project until every source file is reconstructed
    # and every video frame sequence has been encoded and frame-count-checked.
    with tempfile.TemporaryDirectory(prefix='nightjar-event-media-') as tmp:
        temp = Path(tmp)
        local = {}
        for item in uploads:
            folder = setup.upload_dir / item.id
            destination = temp / (item.id + Path(item.name).suffix.lower())
            offset = 0
            with destination.open('wb') as output:
                while offset < item.size:
                    block = folder / str(offset)
                    if not block.exists():
                        raise WorkflowError('Staged dataset file is incomplete.')
                    data = block.read_bytes()
                    if not data or offset + len(data) > item.size:
                        raise WorkflowError('Corrupt staged dataset chunk.')
                    output.write(data)
                    offset += len(data)
            local[_canonical_path(paths[item.id])] = destination
        remote = [
            (row['path'], local[row['path']]) for row in manifest['images']
        ]
        original_sequences = {}
        for index, sequence in enumerate(manifest['videos']):
            title = sequence['title']
            generated = f"videos/{sequence['category']}/{title}.mp4"
            if generated.casefold() in {name.casefold() for name, _ in remote}:
                raise WorkflowError('The generated video path conflicts with an image path.')
            destination = temp / f'video-{index:06d}.mp4'
            encode_video([local[p] for p in sequence['frames']], destination, fps=fps)
            remote.append((generated, destination))
            original_sequences[generated] = sequence
        import supervisely as sly
        try:
            meta = sly.ProjectMeta.from_json(meta or pilot_meta()).to_json()
        except Exception:
            raise WorkflowError('Invalid Supervisely annotation metadata.') from None
        workspace = gateway.api.workspace.get_info_by_id(positive(workspace_id, 'Workspace ID'))
        if not workspace or workspace.team_id != team_id:
            raise WorkflowError('Configured workspace no longer belongs to the event team.')
        details = {
            'name': name.strip(), 'source_count':len(remote),
            'image_count':manifest['image_count'],
            'video_count':manifest['video_count'],
            'original_frame_count':manifest['frame_count'],
            'tree_hash':manifest['hash'], 'fps':int(fps),
        }
        with setup.operation(actor, operation_id, 'structured_dataset_upload', details) as checkpoint:
            # Mark inputs consumed before an irreversible remote write.
            with setup.sessions.begin() as session:
                for item in uploads:
                    changed = session.execute(update(Upload).where(
                        Upload.id == item.id, Upload.consumed.is_(False)
                    ).values(consumed=True))
                    if changed.rowcount != 1:
                        raise WorkflowError('A staged file was already consumed by another upload.')
            result = gateway.upload(
                team_id, workspace_id, name.strip(), 'mixed',
                remote, meta, checkpoint
            )
            records_by_path = {row['relative_path']: row for row in result['sources']}
            with setup.sessions.begin() as session:
                for row in result['sources']:
                    session.merge(SourceOrigin(**row))
                for generated, sequence in original_sequences.items():
                    uploaded = records_by_path.get(generated)
                    if not uploaded:
                        raise WorkflowError(
                            'A converted source video is missing from the upload response.'
                        )
                    session.add(MediaSequence(
                        source_id=uploaded['source_id'],
                        source_folder=sequence['source_folder'],
                        category=sequence['category'],
                        title=sequence['title'],
                        frame_paths=sequence['frames'],
                        frame_count=sequence['frame_count'],
                        fps=int(fps),
                    ))
            checkpoint({'committed_source_assets':len(result['sources'])})
        # Never delete staging data after an uncertain remote operation.
        for item in uploads:
            import shutil
            shutil.rmtree(setup.upload_dir / item.id, ignore_errors=True)
    return {**result, 'manifest':{
        'image_count':manifest['image_count'],
        'video_count':manifest['video_count'],
        'frame_count':manifest['frame_count'],
        'tree_hash':manifest['hash'],
    }}
