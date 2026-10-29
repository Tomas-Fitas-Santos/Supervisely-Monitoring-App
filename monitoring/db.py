from datetime import datetime, timezone

from sqlalchemy import JSON, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def now() -> int:
    return int(datetime.now(timezone.utc).timestamp())


class Base(DeclarativeBase):
    pass


class Team(Base):
    __tablename__ = "participant_teams"
    id: Mapped[int] = mapped_column(primary_key=True)  # Supervisely team ID
    name: Mapped[str] = mapped_column(String(180))
    monitor_id: Mapped[int]
    annotator_ids: Mapped[list] = mapped_column(JSON)
    revision: Mapped[int] = mapped_column(default=0)


class Batch(Base):
    __tablename__ = "batches"
    __table_args__ = (UniqueConstraint("team_id", "position"),)
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("participant_teams.id"), index=True)
    position: Mapped[int]
    kind: Mapped[str] = mapped_column(String(20))
    dataset_id: Mapped[int]
    # Every entry maps a team-local entity to the immutable source asset ID.
    assets: Mapped[list] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(30), default="locked")
    job_ids: Mapped[list] = mapped_column(JSON, default=list)
    remote_status: Mapped[str] = mapped_column(String(100), default="not_created")
    completed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    synced_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    activity: Mapped[dict] = mapped_column(JSON, default=dict)
    activity_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    release_key: Mapped[str | None] = mapped_column(String(80), nullable=True)


class Review(Base):
    __tablename__ = "reviews"
    __table_args__ = (UniqueConstraint("batch_id", "entity_id", "frame_index"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey("batches.id"), index=True)
    entity_id: Mapped[int]
    frame_index: Mapped[int] = mapped_column(default=-1)  # -1 for images
    decision: Mapped[str] = mapped_column(String(25))
    note: Mapped[str] = mapped_column(Text)
    monitor_id: Mapped[int]
    updated_at: Mapped[int] = mapped_column(default=now)


class Audit(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(index=True)
    batch_id: Mapped[str]
    actor_id: Mapped[int]
    action: Mapped[str] = mapped_column(String(40))
    details: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[int] = mapped_column(default=now)


class Lease(Base):
    __tablename__ = "worker_leases"
    name: Mapped[str] = mapped_column(String(60), primary_key=True)
    expires_at: Mapped[int] = mapped_column(default=0)
    owner: Mapped[str] = mapped_column(String(80), default="")


class SetupState(Base):
    __tablename__ = "setup_state"
    id: Mapped[int] = mapped_column(primary_key=True)
    operation_id: Mapped[str | None] = mapped_column(String(80), nullable=True)


class SetupOperation(Base):
    __tablename__ = "setup_operations"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    actor_id: Mapped[int]
    kind: Mapped[str] = mapped_column(String(30))
    state: Mapped[str] = mapped_column(String(30))
    details: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[int] = mapped_column(default=now)


class Upload(Base):
    __tablename__ = "staged_uploads"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    actor_id: Mapped[int]
    name: Mapped[str] = mapped_column(String(180))
    size: Mapped[int]
    offset: Mapped[int] = mapped_column(default=0)
    consumed: Mapped[bool] = mapped_column(default=False)


class EventConfig(Base):
    __tablename__ = "event_config"
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int | None] = mapped_column(nullable=True)
    server_address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    owner_login: Mapped[str | None] = mapped_column(String(180), nullable=True)
    monitoring_team_id: Mapped[int | None] = mapped_column(nullable=True)
    annotator_team_id: Mapped[int | None] = mapped_column(nullable=True)
    source_workspace_id: Mapped[int | None] = mapped_column(nullable=True)
    # Only a local organiser credential is persisted. Hosted session tokens are never stored.
    local_token: Mapped[str | None] = mapped_column(Text, nullable=True)


class EventMember(Base):
    __tablename__ = "event_members"
    user_id: Mapped[int] = mapped_column(primary_key=True)
    group: Mapped[str] = mapped_column(String(20), index=True)
    login: Mapped[str] = mapped_column(String(180), unique=True)
    name: Mapped[str] = mapped_column(String(180))


class CompetitionConfig(Base):
    __tablename__ = 'competition_config'
    id: Mapped[int] = mapped_column(primary_key=True)
    revision: Mapped[int] = mapped_column(default=0)
    frozen: Mapped[bool] = mapped_column(default=False)
    rules: Mapped[dict] = mapped_column(JSON, default=dict)


class Registration(Base):
    __tablename__ = 'registrations'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    team_name: Mapped[str] = mapped_column(String(180), unique=True)
    members: Mapped[list] = mapped_column(JSON)
    monitor_id: Mapped[int] = mapped_column(default=0)
    team_id: Mapped[int | None] = mapped_column(nullable=True)
    error: Mapped[str] = mapped_column(Text, default='')


class Submission(Base):
    __tablename__ = 'submissions'
    __table_args__ = (UniqueConstraint('batch_id', 'version'),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey('batches.id'), index=True)
    version: Mapped[int]
    digest: Mapped[str] = mapped_column(String(64))
    captured_at: Mapped[int] = mapped_column(default=now)
    reason: Mapped[str] = mapped_column(String(30), default='submission')
    payload: Mapped[dict] = mapped_column(JSON)


class TeamOnboarding(Base):
    """Invitation-first provisioning state, including teams not ready for jobs."""
    __tablename__ = 'team_onboarding'
    registration_id: Mapped[str] = mapped_column(ForeignKey('registrations.id'), primary_key=True)
    manager_email: Mapped[str] = mapped_column(String(254), default='')
    memberships: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(30), default='not_created')
    checked_at: Mapped[int | None] = mapped_column(nullable=True)


class WorkUnit(Base):
    __tablename__ = 'work_units'
    __table_args__ = (UniqueConstraint('batch_id', 'entity_id', 'frame_index'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey('batches.id'), index=True)
    entity_id: Mapped[int]
    frame_index: Mapped[int] = mapped_column(default=-1)
    details: Mapped[dict] = mapped_column(JSON)
    synced_at: Mapped[int] = mapped_column(default=now)


class Assessment(Base):
    __tablename__ = 'assessments'
    __table_args__ = (UniqueConstraint('submission_id', 'entity_id', 'frame_index', 'bird_id', 'criterion'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    submission_id: Mapped[str] = mapped_column(ForeignKey('submissions.id'), index=True)
    entity_id: Mapped[int]
    frame_index: Mapped[int]
    bird_id: Mapped[str] = mapped_column(String(100))
    criterion: Mapped[str] = mapped_column(String(50))
    verdict: Mapped[str] = mapped_column(String(20))
    note: Mapped[str] = mapped_column(Text, default='')
    monitor_id: Mapped[int]
    revision: Mapped[int] = mapped_column(default=0)
    updated_at: Mapped[int] = mapped_column(default=now)


class Simulation(Base):
    """Local demonstration resources; never used by the live gateway."""
    __tablename__ = 'simulation'
    id: Mapped[int] = mapped_column(primary_key=True)
    revision: Mapped[int] = mapped_column(default=0)
    data: Mapped[dict] = mapped_column(JSON)


class UploadedPath(Base):
    __tablename__ = 'uploaded_paths'
    upload_id: Mapped[str] = mapped_column(ForeignKey('staged_uploads.id'), primary_key=True)
    path: Mapped[str] = mapped_column(String(1000))


class SourceOrigin(Base):
    __tablename__ = 'source_origins'
    source_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    relative_path: Mapped[str] = mapped_column(String(1000))
    checksum: Mapped[str] = mapped_column(String(64))


class EventClosure(Base):
    __tablename__ = 'event_closure'
    id: Mapped[int] = mapped_column(primary_key=True)
    closed_at: Mapped[int] = mapped_column(default=now)
    deadline: Mapped[int | None] = mapped_column(nullable=True)
    operation_id: Mapped[str] = mapped_column(String(80))


class TaskAttempt(Base):
    __tablename__ = 'task_attempts'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey('batches.id'), index=True)
    parent_job_ids: Mapped[list] = mapped_column(JSON)
    job_ids: Mapped[list] = mapped_column(JSON)
    report: Mapped[str] = mapped_column(Text)
    created_at: Mapped[int] = mapped_column(default=now)


class TaskProgress(Base):
    __tablename__ = 'task_progress'
    job_id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey('batches.id'), index=True)
    details: Mapped[dict] = mapped_column(JSON)


def database(url: str):
    opts = {"connect_args": {"check_same_thread": False, "timeout": 30}} if url.startswith("sqlite") else {}
    engine = create_engine(url, pool_pre_ping=True, **opts)
    return engine, sessionmaker(engine, expire_on_commit=False)


def initialize(engine):
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine)
    with factory.begin() as session:
        if not session.get(Lease, "poll"):
            session.add(Lease(name="poll"))
        if not session.get(SetupState, 1):
            session.add(SetupState(id=1))
        if not session.get(EventConfig, 1):
            session.add(EventConfig(id=1))
        if not session.get(CompetitionConfig, 1):
            session.add(CompetitionConfig(id=1, rules={}))
