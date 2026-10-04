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
