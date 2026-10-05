"""Only this module knows Supervisely SDK calls; no tokens enter browser state."""
from collections import Counter
from datetime import datetime, timedelta, timezone

from .service import WorkflowError


class Gateway:
    def __init__(self, api):
        self.api = api

    def member(self, monitoring_team_id, user_id):
        member = self.api.user.get_member_info_by_id(monitoring_team_id, user_id)
        if not member or getattr(member, 'disabled', False):
            raise WorkflowError("You must be a member of the monitoring team.")

    def review_preview(self, team, batch, entity_id, frame_index):
        """Read-only preview of the selected annotation; never accepts an arbitrary entity."""
        import base64
        import cv2
        import supervisely as sly
        asset = next((a for a in batch.assets if a['entity_id'] == entity_id), None)
        if not asset:
            raise WorkflowError('Choose an image or video in this batch.')
        if ((batch.kind == 'images' and frame_index != -1)
                or (batch.kind == 'video' and not 0 <= frame_index < asset['frames'])):
            raise WorkflowError('Choose a valid frame; video frame numbers start at zero.')
        dataset = self.api.dataset.get_info_by_id(batch.dataset_id)
        if not dataset:
            raise WorkflowError('Dataset is unavailable.')
        project = self.api.project.get_info_by_id(dataset.project_id)
        workspace = self.api.workspace.get_info_by_id(project.workspace_id)
        if workspace.team_id != team.id or project.type != ('images' if batch.kind == 'images' else 'videos'):
            raise WorkflowError('This dataset does not belong to the selected participant team.')
        media_api = self.api.image if batch.kind == 'images' else self.api.video
        info = media_api.get_info_by_id(entity_id)
        if not info or info.dataset_id != batch.dataset_id:
            raise WorkflowError('The selected media no longer belongs to this batch.')
        meta = sly.ProjectMeta.from_json(self.api.project.get_meta(project.id))
        if batch.kind == 'images':
            image = self.api.image.download_np(entity_id)
            annotation = sly.Annotation.from_json(self.api.annotation.download_json(entity_id), meta)
        else:
            image = self.api.video.frame.download_np(entity_id, frame_index)
            video = sly.VideoAnnotation.from_json(self.api.video.annotation.download(entity_id), meta)
            frame = video.frames.get(frame_index)
            labels = [sly.Label(f.geometry, f.video_object.obj_class) for f in frame.figures] if frame else []
            annotation = sly.Annotation(image.shape[:2], labels)
        annotated = image.copy()
        annotation.draw(annotated, thickness=3, fill_rectangles=False)
        def encode(bitmap):
            height, width = bitmap.shape[:2]
            scale = min(1, 1280 / max(height, width))
            if scale < 1:
                bitmap = cv2.resize(bitmap, (max(1, round(width * scale)), max(1, round(height * scale))))
            ok, encoded = cv2.imencode('.jpg', cv2.cvtColor(bitmap, cv2.COLOR_RGB2BGR),
                                      [cv2.IMWRITE_JPEG_QUALITY, 88])
            if not ok:
                raise WorkflowError('Could not render the selected image or frame.')
            return 'data:image/jpeg;base64,' + base64.b64encode(encoded).decode()
        return {'team_id': team.id, 'batch_id': batch.id, 'entity_id': entity_id, 'frame_index': frame_index,
                'original': encode(image), 'annotated': encode(annotated), 'labels': len(annotation.labels),
                'classes': [{'name': name, 'color': color} for name, color in
                    {label.obj_class.name: label.obj_class.color for label in annotation.labels}.items()],
                'loaded_at': int(datetime.now(timezone.utc).timestamp())}

    def validate(self, team, batch):
        dataset = self.api.dataset.get_info_by_id(batch.dataset_id)
        if not dataset:
            raise WorkflowError("Dataset is unavailable.")
        project = self.api.project.get_info_by_id(dataset.project_id)
        workspace = self.api.workspace.get_info_by_id(project.workspace_id)
        if workspace.team_id != team.id or project.type != ('images' if batch.kind == 'images' else 'videos'):
            raise WorkflowError("Dataset type or participant team does not match the manifest.")
        for uid in [*team.annotator_ids, team.monitor_id]:
            if not self.api.user.get_member_info_by_id(team.id, uid):
                raise WorkflowError("Add both annotators and their monitor to the participant team first.")
        api = self.api.image if batch.kind == 'images' else self.api.video
        for asset in batch.assets:
            info = api.get_info_by_id(asset['entity_id'])
            if not info or info.dataset_id != batch.dataset_id:
                raise WorkflowError("A mapped entity does not belong to the batch dataset.")
            if batch.kind == 'video' and info.frames_count != asset['frames']:
                raise WorkflowError("Video frame count does not match the manifest.")

    @staticmethod
    def slices(team, batch):
        # Explicit non-overlapping image jobs. Full video assigned to one lead.
        ids = [a['entity_id'] for a in batch.assets]
        if batch.kind == 'video':
            return [(team.annotator_ids[0], ids)]
        return [(uid, ids[index::len(team.annotator_ids)])
                for index, uid in enumerate(team.annotator_ids) if ids[index::len(team.annotator_ids)]]

    def find_release(self, team, batch):
        found = []
        jobs = self.api.labeling_job.get_list(team.id, dataset_id=batch.dataset_id, show_disabled=True)
        for uid, ids in self.slices(team, batch):
            name = f'{batch.release_key}:u{uid}'
            matches = [j for j in jobs if j.name == name and j.assigned_to_id == uid]
            if len(matches) > 1:
                raise WorkflowError("Multiple remote jobs match this release. Organiser reconciliation required.")
            if matches:
                job = matches[0]
                if job.dataset_id != batch.dataset_id or job.images_count != len(ids) or job.disabled:
                    raise WorkflowError("Remote release does not match the expected task.")
                # Validate explicit membership rather than relying on the count alone.
                entities = job.entities
                remote_ids = {e['id'] if isinstance(e, dict) else e for e in entities}
                if remote_ids != set(ids):
                    raise WorkflowError("Remote release entity IDs differ from the manifest.")
                found.append(job.id)
        return found

    def create_release(self, team, batch):
        created = []
        for uid, ids in self.slices(team, batch):
            jobs = self.api.labeling_job.create(
                name=f'{batch.release_key}:u{uid}', dataset_id=batch.dataset_id,
                user_ids=[uid], images_ids=ids, reviewer_id=team.monitor_id,
                description='Nightjar independent team annotations. Submit for monitor review.',
                enable_quality_check=True, dynamic_classes=False, dynamic_tags=False,
                readme='Annotate each bird: bounding box, crown, left_eye, right_eye, beak and per-keypoint visibility. '
                       'Use the event anatomical guide. The monitor will release your next task.',
            )
            if len(jobs) != 1:
                raise WorkflowError("Unexpected number of jobs returned by Supervisely.")
            created.append(jobs[0].id)
        return created

    def progress(self, batch):
        statuses, total, completed = [], 0, 0
        for jid in batch.job_ids:
            info = self.api.labeling_job.get_info_by_id(jid)
            if not info:
                raise WorkflowError("A labeling job is missing.")
            statuses.append(info.status)
            stats = self.api.labeling_job.get_stats(jid)['job']
            total += int(stats['imagesCount'])
            completed += int(stats['finishedImagesCount'])
        return {'status': ','.join(statuses), 'total': total, 'completed': completed}

    def recent_activity(self, team, batch, minutes=15):
        since = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
        events = []
        for jid in batch.job_ids:
            events.extend(self.api.team.get_activity(team.id, filter_job_id=jid, start_date=since))
        labeling_actions = {'create_figure', 'update_figure', 'disable_figure', 'restore_figure',
                            'attach_tag', 'update_tag_value', 'detach_tag'}
        events = [e for e in events if e.get('userId') in team.annotator_ids
                  and str(e.get('action', '')).lower() in labeling_actions]
        counts = Counter(e['userId'] for e in events)
        return {'window_minutes': minutes, 'actions_by_user': {str(u): counts[u] for u in team.annotator_ids},
                'last_action_at': max((e.get('createdAt', '') for e in events), default=None)}

    def job_url(self, job_id):
        job = self.api.labeling_job.get_info_by_id(job_id)
        project = self.api.project.get_info_by_id(job.project_id)
        kind = 'videos' if project.type == 'videos' else 'images'
        return self.api.server_address.rstrip('/') + (
            f'/app/{kind}/{job.team_id}/{job.workspace_id}/{job.project_id}/{job.dataset_id}?jobId={job.id}')

    def publish_image_review(self, team, batch, entity_id, decision):
        if batch.kind != 'images':
            raise WorkflowError('Video frame review remains an app record; it cannot mark an entire video accepted.')
        for index, (_, ids) in enumerate(self.slices(team, batch)):
            if entity_id in ids:
                self.api.labeling_job.set_entity_review_status(batch.job_ids[index], entity_id,
                    'accepted' if decision == 'accepted' else 'rejected')
                return
        raise WorkflowError('Image is outside the job.')
