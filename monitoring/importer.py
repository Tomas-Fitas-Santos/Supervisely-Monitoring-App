from collections import Counter

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, model_validator

from .db import Batch, Team


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class AssetSpec(StrictModel):
    source_id: str = Field(min_length=1, max_length=200)
    entity_id: PositiveInt
    frames: PositiveInt | None = None


class BatchSpec(StrictModel):
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,80}$')
    kind: str
    dataset_id: PositiveInt
    assets: list[AssetSpec] = Field(min_length=1)

    @model_validator(mode='after')
    def valid_kind(self):
        if self.kind not in ('images', 'video'):
            raise ValueError('kind must be images or video')
        if self.kind == 'video' and (len(self.assets) != 1 or self.assets[0].frames is None):
            raise ValueError('A video batch must contain exactly one complete video and its frame count')
        if self.kind == 'images' and any(a.frames is not None for a in self.assets):
            raise ValueError('Image assets cannot declare frames')
        return self


class TeamSpec(StrictModel):
    id: PositiveInt
    name: str = Field(min_length=1, max_length=180)
    monitor_id: PositiveInt
    annotator_ids: list[PositiveInt] = Field(min_length=2, max_length=2)
    batches: list[BatchSpec] = Field(min_length=1)

    @model_validator(mode='after')
    def valid_sequence(self):
        if len(set([self.monitor_id, *self.annotator_ids])) != 3:
            raise ValueError('Two distinct annotators and a distinct monitor are required')
        videos = [b.kind for b in self.batches].count('video')
        if videos > 1 or (videos and self.batches[-1].kind != 'video'):
            raise ValueError('At most one complete video is allowed, as the final batch')
        return self


class Manifest(StrictModel):
    teams: list[TeamSpec] = Field(min_length=1)

    @model_validator(mode='after')
    def independent(self):
        team_ids, batch_ids, destinations, sources = set(), set(), set(), {}
        annotators = set()
        for team in self.teams:
            if team.id in team_ids or annotators.intersection(team.annotator_ids):
                raise ValueError('Team IDs and annotator memberships must be distinct')
            team_ids.add(team.id)
            annotators.update(team.annotator_ids)
            own_sources = set()
            for batch in team.batches:
                if batch.id in batch_ids:
                    raise ValueError('Batch IDs must be globally unique')
                batch_ids.add(batch.id)
                for asset in batch.assets:
                    key = (batch.kind, asset.entity_id)
                    if key in destinations or asset.source_id in own_sources:
                        raise ValueError('Independent versions need distinct destination entities; no repeated source within a team')
                    destinations.add(key)
                    own_sources.add(asset.source_id)
                    previous = sources.setdefault(asset.source_id, (batch.kind, asset.frames))
                    if previous != (batch.kind, asset.frames):
                        raise ValueError('Source type or video length is inconsistent across replicas')
        return self

    def coverage(self):
        counts = Counter(a.source_id for t in self.teams for b in t.batches for a in b.assets)
        return {'replicas_by_source': dict(counts), 'under_replicated': [k for k, n in counts.items() if n < 3]}


def import_manifest(sessions, manifest, gateway=None, monitoring_team_id=None, pilot=False):
    if not pilot and any(t.batches[-1].kind != 'video' for t in manifest.teams):
        raise ValueError('Each full-event team requires a final video. Use --pilot for an image-only rehearsal.')
    if manifest.coverage()['under_replicated'] and not pilot:
        raise ValueError('Each source requires at least three teams. Use --pilot for an explicitly reduced rehearsal.')
    if gateway:
        for t in manifest.teams:
            gateway.member(monitoring_team_id, t.monitor_id)
            team = Team(id=t.id, name=t.name, monitor_id=t.monitor_id, annotator_ids=t.annotator_ids)
            for b in t.batches:
                gateway.validate(team, Batch(id=b.id, kind=b.kind, dataset_id=b.dataset_id,
                    assets=[a.model_dump(exclude_none=True) for a in b.assets]))
    with sessions.begin() as s:
        # Initial import only. Re-import cannot overwrite live decisions or assignments.
        from sqlalchemy import select
        if s.scalar(select(Team.id).limit(1)) is not None:
            raise ValueError('Database already contains an event; refusing to overwrite it')
        for team in manifest.teams:
            s.add(Team(id=team.id, name=team.name, monitor_id=team.monitor_id, annotator_ids=team.annotator_ids))
        s.flush()
        for team in manifest.teams:
            for i, b in enumerate(team.batches, 1):
                s.add(Batch(id=b.id, team_id=team.id, position=i, kind=b.kind, dataset_id=b.dataset_id,
                            assets=[a.model_dump(exclude_none=True) for a in b.assets]))
