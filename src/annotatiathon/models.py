"""Persistent event records. All account and attendee identifiers are application-owned."""
import time
import uuid

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def epoch() -> int:
    return int(time.time())


def identifier() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


DEFAULT_EVENT_ID = "00000000-0000-4000-8000-000000000010"


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (CheckConstraint(
        "status IN ('active','archived','finalizing','resetting')", name="ck_event_status",
    ), CheckConstraint(
        "(start_at IS NULL AND end_at IS NULL) OR "
        "(start_at IS NOT NULL AND end_at IS NOT NULL AND end_at > start_at)",
        name="ck_event_schedule_valid",
    ),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_at: Mapped[int] = mapped_column(Integer, default=epoch)
    archived_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    closing_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    closing_by: Mapped[str | None] = mapped_column(ForeignKey("staff_accounts.id"), nullable=True)
    start_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reset_from_id: Mapped[str | None] = mapped_column(
        ForeignKey("events.id"), nullable=True
    )


class ResetPlan(Base):
    __tablename__ = "event_reset_plans"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), index=True)
    staff_id: Mapped[str] = mapped_column(ForeignKey("staff_accounts.id"))
    mode: Mapped[str] = mapped_column(String(20))
    areas: Mapped[list[str]] = mapped_column(JSON, default=list)
    digest: Mapped[str] = mapped_column(String(64))
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    expires_at: Mapped[int] = mapped_column(Integer)
    executed_at: Mapped[int | None] = mapped_column(Integer, nullable=True)


class EventSnapshot(Base):
    __tablename__ = "event_snapshots"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), index=True)
    reset_plan_id: Mapped[str] = mapped_column(ForeignKey("event_reset_plans.id"))
    snapshot_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[int] = mapped_column(Integer, default=epoch)


class ApprovedStaffEmail(Base):
    """Nightjar login allowlist, completely independent of event membership."""
    __tablename__ = "approved_staff_emails"
    email: Mapped[str] = mapped_column(String(320), primary_key=True)
    role: Mapped[str] = mapped_column(String(20))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[int] = mapped_column(Integer, default=epoch)


class StaffAccount(Base):
    __tablename__ = "staff_accounts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(Text)
    roles: Mapped[list[str]] = mapped_column(JSON, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    login_failures: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # A verified native identity link only. Personal API tokens are never stored.
    supervisely_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    supervisely_login: Mapped[str | None] = mapped_column(String(320), nullable=True)
    supervisely_role: Mapped[str | None] = mapped_column(String(40), nullable=True)
    supervisely_verified_team_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    supervisely_verified_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[int] = mapped_column(Integer, default=epoch)


class RegistrationChallenge(Base):
    """Single-use email verification; only pre-registered event monitors qualify."""

    __tablename__ = "registration_challenges"
    email: Mapped[str] = mapped_column(String(320), primary_key=True)
    code_digest: Mapped[str] = mapped_column(String(128))
    expires_at: Mapped[int] = mapped_column(Integer)
    requested_at: Mapped[int] = mapped_column(Integer)
    attempts: Mapped[int] = mapped_column(Integer, default=0)


class StaffSession(Base):
    __tablename__ = "staff_sessions"
    event_id: Mapped[str | None] = mapped_column(ForeignKey("events.id"), nullable=True)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    staff_id: Mapped[str] = mapped_column(ForeignKey("staff_accounts.id"), index=True)
    csrf_token: Mapped[str] = mapped_column(String(128))
    expires_at: Mapped[int] = mapped_column(Integer, index=True)
    created_at: Mapped[int] = mapped_column(Integer, default=epoch)


class CompetitionTeam(Base):
    __tablename__ = "competition_teams"
    __table_args__ = (UniqueConstraint("event_id", "name", name="uq_competition_event_name"),)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    name: Mapped[str] = mapped_column(String(200), index=True)
    table_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[int] = mapped_column(Integer, default=epoch)


class Person(Base):
    __tablename__ = "people"
    __table_args__ = (UniqueConstraint("event_id", "email", name="uq_person_event_email"),)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), index=True)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    name: Mapped[str] = mapped_column(String(200), index=True)
    email: Mapped[str] = mapped_column(String(320), index=True)
    kind: Mapped[str] = mapped_column(String(20), index=True)
    team_id: Mapped[str | None] = mapped_column(
        ForeignKey("competition_teams.id"), index=True, nullable=True
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # This value is never set to "joined" by a roster import or a browser claim.
    membership_status: Mapped[str] = mapped_column(String(30), default="not_invited")
    supervisely_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    supervisely_login: Mapped[str | None] = mapped_column(String(320), nullable=True)
    supervisely_role: Mapped[str | None] = mapped_column(String(40), nullable=True)
    native_verified_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    check_in_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    table_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_fields: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[int] = mapped_column(Integer, default=epoch)
    updated_at: Mapped[int] = mapped_column(Integer, default=epoch, onupdate=epoch)


class SuperviselyConfig(Base):
    __tablename__ = "supervisely_config"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    server_url: Mapped[str] = mapped_column(
        String(512), default="https://app.supervisely.com"
    )
    team_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    workspace_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    token_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    last_verified_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    updated_at: Mapped[int] = mapped_column(Integer, default=epoch, onupdate=epoch)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    event_id: Mapped[str | None] = mapped_column(ForeignKey("events.id"), index=True, nullable=True)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    staff_id: Mapped[str | None] = mapped_column(
        ForeignKey("staff_accounts.id"), nullable=True, index=True
    )
    action: Mapped[str] = mapped_column(String(100), index=True)
    target_type: Mapped[str] = mapped_column(String(80))
    target_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[int] = mapped_column(Integer, default=epoch, index=True)


class ImportBatch(Base):
    __tablename__ = "roster_imports"
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), index=True)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    source_hash: Mapped[str] = mapped_column(String(64), index=True)
    staff_id: Mapped[str] = mapped_column(ForeignKey("staff_accounts.id"))
    row_count: Mapped[int] = mapped_column(Integer)
    created_count: Mapped[int] = mapped_column(Integer)
    changed_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[int] = mapped_column(Integer, default=epoch)


class TicketCategory(Base):
    __tablename__ = "ticket_categories"
    __table_args__ = (UniqueConstraint("event_id", "slug", name="uq_ticket_event_slug"),)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), index=True)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    slug: Mapped[str] = mapped_column(String(80), index=True)
    name: Mapped[str] = mapped_column(String(100))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    warning_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    warning_threshold: Mapped[int | None] = mapped_column(Integer, nullable=True)
    warning_window_minutes: Mapped[int] = mapped_column(Integer, default=60)


class TicketDistribution(Base):
    """Snapshot of recipients before an organiser/distributor confirms a batch."""

    __tablename__ = "ticket_distributions"
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), index=True)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    category_id: Mapped[str] = mapped_column(ForeignKey("ticket_categories.id"))
    operation: Mapped[str] = mapped_column(String(20))
    quantity: Mapped[int] = mapped_column(Integer)
    recipient_ids: Mapped[list[str]] = mapped_column(JSON)
    scope: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(20), default="preview")
    requested_by: Mapped[str] = mapped_column(ForeignKey("staff_accounts.id"))
    confirmed_by: Mapped[str | None] = mapped_column(ForeignKey("staff_accounts.id"), nullable=True)
    created_at: Mapped[int] = mapped_column(Integer, default=epoch)
    confirmed_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reversed_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reversal_staff_id: Mapped[str | None] = mapped_column(
        ForeignKey("staff_accounts.id"), nullable=True
    )


class TicketLedgerEntry(Base):
    """Signed append-only ledger. One row per balance change, including reversals."""

    __tablename__ = "ticket_ledger"
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), index=True)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    category_id: Mapped[str] = mapped_column(ForeignKey("ticket_categories.id"), index=True)
    person_id: Mapped[str] = mapped_column(ForeignKey("people.id"), index=True)
    distribution_id: Mapped[str] = mapped_column(
        ForeignKey("ticket_distributions.id"), index=True
    )
    kind: Mapped[str] = mapped_column(String(15), index=True)
    quantity: Mapped[int] = mapped_column(Integer)
    staff_id: Mapped[str] = mapped_column(ForeignKey("staff_accounts.id"))
    reversal_of: Mapped[str | None] = mapped_column(
        ForeignKey("ticket_ledger.id"), nullable=True, unique=True
    )
    created_at: Mapped[int] = mapped_column(Integer, default=epoch, index=True)


class Activity(Base):
    """Event-local activity. Award finalisation freezes results and prize rules."""

    __tablename__ = "activities"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    location: Mapped[str] = mapped_column(String(200), default="")
    start_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="scheduled")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    awards_finalised_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("staff_accounts.id"))
    created_at: Mapped[int] = mapped_column(Integer, default=epoch)
    updated_at: Mapped[int] = mapped_column(Integer, default=epoch, onupdate=epoch)


class ActivityResult(Base):
    """One individual or one competition team's rank and optional score per activity."""

    __tablename__ = "activity_results"
    __table_args__ = (
        UniqueConstraint("activity_id", "subject_type", "subject_id",
                         name="uq_activity_subject_result"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), index=True)
    activity_id: Mapped[str] = mapped_column(ForeignKey("activities.id"), index=True)
    subject_type: Mapped[str] = mapped_column(String(20))
    subject_id: Mapped[str] = mapped_column(String(36))
    rank: Mapped[int] = mapped_column(Integer)
    score_points: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    recorded_by: Mapped[str] = mapped_column(ForeignKey("staff_accounts.id"))
    created_at: Mapped[int] = mapped_column(Integer, default=epoch)
    updated_at: Mapped[int] = mapped_column(Integer, default=epoch, onupdate=epoch)


class ActivityPrize(Base):
    """A rank interval awards a ticket entitlement to each qualifying participant."""

    __tablename__ = "activity_prizes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), index=True)
    activity_id: Mapped[str] = mapped_column(ForeignKey("activities.id"), index=True)
    category_id: Mapped[str] = mapped_column(ForeignKey("ticket_categories.id"))
    min_rank: Mapped[int] = mapped_column(Integer)
    max_rank: Mapped[int] = mapped_column(Integer)
    quantity: Mapped[int] = mapped_column(Integer)
    created_by: Mapped[str] = mapped_column(ForeignKey("staff_accounts.id"))
    created_at: Mapped[int] = mapped_column(Integer, default=epoch)


class ActivityAward(Base):
    """Immutable provenance tying an award recipient to an allocated ticket batch."""

    __tablename__ = "activity_awards"
    __table_args__ = (
        UniqueConstraint("activity_id", "prize_id", "person_id",
                         name="uq_activity_prize_recipient"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), index=True)
    activity_id: Mapped[str] = mapped_column(ForeignKey("activities.id"), index=True)
    prize_id: Mapped[str] = mapped_column(ForeignKey("activity_prizes.id"))
    person_id: Mapped[str] = mapped_column(ForeignKey("people.id"))
    distribution_id: Mapped[str] = mapped_column(ForeignKey("ticket_distributions.id"))
    awarded_at: Mapped[int] = mapped_column(Integer, default=epoch)


class NativeSyncPlan(Base):
    """Ten-minute, event/staff-scoped review of authoritative team membership."""

    __tablename__ = "native_sync_plans"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), index=True)
    staff_id: Mapped[str] = mapped_column(ForeignKey("staff_accounts.id"))
    team_id: Mapped[int] = mapped_column(Integer)
    source_digest: Mapped[str] = mapped_column(String(64))
    roster_digest: Mapped[str] = mapped_column(String(64))
    associations: Mapped[list[dict]] = mapped_column(JSON)
    expires_at: Mapped[int] = mapped_column(Integer)
    applied_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[int] = mapped_column(Integer, default=epoch)


class MonitorAssignment(Base):
    """Event-local responsibility assignment; never changes native Supervisely roles."""

    __tablename__ = "monitor_assignments"
    __table_args__ = (UniqueConstraint("team_id", name="uq_monitor_assignment_team"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), index=True)
    team_id: Mapped[str] = mapped_column(ForeignKey("competition_teams.id"), index=True)
    monitor_person_id: Mapped[str] = mapped_column(ForeignKey("people.id"), index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    assigned_by: Mapped[str] = mapped_column(ForeignKey("staff_accounts.id"))
    created_at: Mapped[int] = mapped_column(Integer, default=epoch)
    updated_at: Mapped[int] = mapped_column(Integer, default=epoch, onupdate=epoch)


class MonitorAssignmentPlan(Base):
    """Short-lived balanced allocation proposal; optimistic snapshot validation."""

    __tablename__ = "monitor_assignment_plans"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), index=True)
    staff_id: Mapped[str] = mapped_column(ForeignKey("staff_accounts.id"))
    mode: Mapped[str] = mapped_column(String(20))
    roster_digest: Mapped[str] = mapped_column(String(64))
    proposals: Mapped[list[dict]] = mapped_column(JSON)
    expires_at: Mapped[int] = mapped_column(Integer)
    applied_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[int] = mapped_column(Integer, default=epoch)


class MediaDataset(Base):
    """Persistent local media collection imported from a ZIP or root folder."""

    __tablename__ = "media_datasets"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    upload_kind: Mapped[str] = mapped_column(String(12))
    status: Mapped[str] = mapped_column(String(24), default="uploading")
    root_path: Mapped[str] = mapped_column(Text)
    source_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    image_count: Mapped[int] = mapped_column(Integer, default=0)
    frame_count: Mapped[int] = mapped_column(Integer, default=0)
    video_count: Mapped[int] = mapped_column(Integer, default=0)
    native_project_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    native_dataset_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    native_manual_dataset_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("staff_accounts.id"))
    created_at: Mapped[int] = mapped_column(Integer, default=epoch)
    completed_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_activity_at: Mapped[int] = mapped_column(Integer, default=epoch)
    error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    extraction_policy: Mapped[str] = mapped_column(String(24), default="sample_1fps")


class MediaVideo(Base):
    """Immutable source identity and the frame collection's extraction provenance."""
    __tablename__ = "media_videos"
    __table_args__ = (UniqueConstraint("dataset_id", "video_group", name="uq_dataset_video_group"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), index=True)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("media_datasets.id"), index=True)
    video_group: Mapped[str] = mapped_column(String(500))
    source_relative_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    extraction_policy: Mapped[str] = mapped_column(String(24))
    density: Mapped[str] = mapped_column(String(24), default="unknown")
    frame_count: Mapped[int] = mapped_column(Integer, default=0)
    source_frame_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    extracted_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    time_base: Mapped[str | None] = mapped_column(String(64), nullable=True)
    frame_rate: Mapped[str | None] = mapped_column(String(64), nullable=True)
    identity_status: Mapped[str] = mapped_column(String(24), default="unknown")
    missing_frame_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    identity_notes: Mapped[list[str]] = mapped_column(JSON, default=list)


class DatasetStagedFile(Base):
    __tablename__ = "dataset_staged_files"
    __table_args__ = (UniqueConstraint("dataset_id", "relative_path", name="uq_stage_path"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("media_datasets.id"), index=True)
    relative_path: Mapped[str] = mapped_column(Text)
    expected_bytes: Mapped[int] = mapped_column(BigInteger)
    received_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[int] = mapped_column(Integer, default=epoch)


class MediaAsset(Base):
    __tablename__ = "media_assets"
    __table_args__ = (
        UniqueConstraint("dataset_id", "relative_path", name="uq_dataset_asset_path"),
        UniqueConstraint("video_id", "frame_index", name="uq_video_frame_ordinal"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), index=True)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("media_datasets.id"), index=True)
    relative_path: Mapped[str] = mapped_column(Text)
    morphology: Mapped[str] = mapped_column(String(200), index=True)
    kind: Mapped[str] = mapped_column(String(20), index=True)
    video_group: Mapped[str | None] = mapped_column(String(500), nullable=True)
    frame_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    video_id: Mapped[str | None] = mapped_column(ForeignKey("media_videos.id"), nullable=True, index=True)
    presentation_timestamp: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    frame_width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    frame_height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    native_image_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    native_dataset_id: Mapped[int | None] = mapped_column(Integer, nullable=True)




class DatasetFolderClassification(Base):
    """Curated mapping from a dataset source folder to an ecomorphology and species."""

    __tablename__ = "dataset_folder_classifications"
    __table_args__ = (
        UniqueConstraint("dataset_id", "source_folder",
                         name="uq_dataset_source_folder_classification"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), index=True)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("media_datasets.id"), index=True)
    source_folder: Mapped[str] = mapped_column(String(200))
    ecomorphology: Mapped[str] = mapped_column(String(200))
    species_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    updated_by: Mapped[str] = mapped_column(ForeignKey("staff_accounts.id"))
    updated_at: Mapped[int] = mapped_column(Integer, default=epoch, onupdate=epoch)


class DatasetMovementSegment(Base):
    """Observed primary movement for a disjoint range of frames in one video.

    Frame indexes are 1-based within the lexicographically ordered filenames of
    that video group. This labels actual frames, not presumed species behavior.
    """

    __tablename__ = "dataset_movement_segments"
    __table_args__ = (
        CheckConstraint("start_frame >= 1 AND end_frame >= start_frame",
                        name="ck_movement_segment_range"),
        Index("ix_movement_dataset_video", "dataset_id", "video_group"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), index=True)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("media_datasets.id"), index=True)
    video_group: Mapped[str] = mapped_column(String(500))
    start_frame: Mapped[int] = mapped_column(Integer)
    end_frame: Mapped[int] = mapped_column(Integer)
    movement_type: Mapped[str] = mapped_column(String(60))
    notes: Mapped[str] = mapped_column(Text, default="")
    labeled_by: Mapped[str] = mapped_column(ForeignKey("staff_accounts.id"))
    labeled_at: Mapped[int] = mapped_column(Integer, default=epoch)


class LabelingPlan(Base):
    __tablename__ = "labeling_plans"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), index=True)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("media_datasets.id"), index=True)
    jobs_per_team: Mapped[int] = mapped_column(Integer)
    redundancy: Mapped[int] = mapped_column(Integer)
    video_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="planned")
    created_by: Mapped[str] = mapped_column(ForeignKey("staff_accounts.id"))
    created_at: Mapped[int] = mapped_column(Integer, default=epoch)
    approved_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    queue_seed: Mapped[str | None] = mapped_column(String(64))


class LabelingJob(Base):
    __tablename__ = "labeling_jobs"
    __table_args__ = (
        UniqueConstraint("plan_id", "team_id", "sequence", name="uq_job_plan_team_seq"),
        Index("ix_jobs_event_order", "event_id", "team_id", "sequence", "id"),
        Index("ix_jobs_media_membership", text("(media_ids::jsonb)"),
              postgresql_using="gin").ddl_if(dialect="postgresql"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), index=True)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("media_datasets.id"))
    plan_id: Mapped[str] = mapped_column(ForeignKey("labeling_plans.id"), index=True)
    team_id: Mapped[str] = mapped_column(ForeignKey("competition_teams.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    assignee_person_id: Mapped[str | None] = mapped_column(
        ForeignKey("people.id"), nullable=True, index=True
    )
    # Manual jobs are never auto-released by the plan scheduler.
    release_mode: Mapped[str] = mapped_column(String(24), default="automatic")
    media_ids: Mapped[list[str]] = mapped_column(JSON)
    frame_count: Mapped[int] = mapped_column(Integer)
    video_count: Mapped[int] = mapped_column(Integer)
    morphology_counts: Mapped[dict[str, int]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(24), default="queued")
    native_dataset_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    review_status: Mapped[str] = mapped_column(String(24), default="unreviewed")
    correction_round: Mapped[int] = mapped_column(Integer, default=0)
    assigned_monitor_id: Mapped[str | None] = mapped_column(
        ForeignKey("people.id"), nullable=True
    )
    created_at: Mapped[int] = mapped_column(Integer, default=epoch)
    started_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    submitted_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completed_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    annotation_snapshot_round: Mapped[int | None] = mapped_column(Integer, nullable=True)
    annotation_snapshot_frames: Mapped[int | None] = mapped_column(Integer, nullable=True)
    annotation_snapshot_annotated_frames: Mapped[int | None] = mapped_column(Integer, nullable=True)


class WorkBlock(Base):
    __tablename__ = "work_blocks"
    __table_args__ = (
        UniqueConstraint("plan_id", "video_id", "ordinal", name="uq_work_block_ordinal"),
        CheckConstraint("frame_count BETWEEN 1 AND 30", name="ck_work_block_size"),
        Index("ix_work_block_repeat_queue", "plan_id", "repeat_team_id", "id",
              postgresql_where=text("repeat_job_id IS NULL")),
        Index("ix_work_block_available", "plan_id", "video_id", "ordinal",
              postgresql_where=text("job_id IS NULL"), sqlite_where=text("job_id IS NULL")),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), index=True)
    plan_id: Mapped[str] = mapped_column(ForeignKey("labeling_plans.id"))
    video_id: Mapped[str] = mapped_column(ForeignKey("media_videos.id"))
    ordinal: Mapped[int] = mapped_column(Integer)
    frame_count: Mapped[int] = mapped_column(Integer)
    media_ids: Mapped[list[str]] = mapped_column(JSON)
    repeat_media_ids: Mapped[list[str]] = mapped_column(JSON)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("labeling_jobs.id"), unique=True)
    repeat_team_id: Mapped[str | None] = mapped_column(ForeignKey("competition_teams.id"))
    repeat_job_id: Mapped[str | None] = mapped_column(ForeignKey("labeling_jobs.id"))


class WorkPacket(Base):
    __tablename__ = "work_packets"
    __table_args__ = (
        Index("uq_open_work_packet", "event_id", "person_id", unique=True,
              postgresql_where=text("submitted_at IS NULL"),
              sqlite_where=text("submitted_at IS NULL")),
    )
    job_id: Mapped[str] = mapped_column(ForeignKey("labeling_jobs.id"), primary_key=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), index=True)
    person_id: Mapped[str] = mapped_column(ForeignKey("people.id"))
    submitted_at: Mapped[int | None] = mapped_column(Integer)


class NativeJobBinding(Base):
    __tablename__ = "native_job_bindings"
    __table_args__ = (
        UniqueConstraint("job_id", "person_id", name="uq_native_binding_person_job"),
        UniqueConstraint("native_job_id", name="uq_native_job_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("labeling_jobs.id"), index=True)
    person_id: Mapped[str] = mapped_column(ForeignKey("people.id"), index=True)
    native_job_id: Mapped[int] = mapped_column(Integer)
    native_status: Mapped[str] = mapped_column(String(30), default="pending")
    last_synced_at: Mapped[int | None] = mapped_column(Integer, nullable=True)


class JobReview(Base):
    __tablename__ = "job_reviews"
    __table_args__ = (
        UniqueConstraint("job_id", "media_id", name="uq_job_media_review"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("labeling_jobs.id"), index=True)
    media_id: Mapped[str] = mapped_column(ForeignKey("media_assets.id"))
    verdict: Mapped[str] = mapped_column(String(24))
    correct_keypoints: Mapped[int] = mapped_column(Integer, default=0)
    missing_keypoints: Mapped[int] = mapped_column(Integer, default=0)
    incorrect_keypoints: Mapped[int] = mapped_column(Integer, default=0)
    visible_keypoints: Mapped[int] = mapped_column(Integer, default=0)
    occluded_keypoints: Mapped[int] = mapped_column(Integer, default=0)
    temporal_consistency: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    reviewer_id: Mapped[str] = mapped_column(ForeignKey("staff_accounts.id"))
    reviewed_at: Mapped[int] = mapped_column(Integer, default=epoch)


class EventCloseout(Base):
    __tablename__ = "event_closeouts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    event_id: Mapped[str] = mapped_column(
        ForeignKey("events.id"), unique=True, index=True
    )
    snapshot_json: Mapped[dict] = mapped_column(JSON)
    closed_by: Mapped[str] = mapped_column(ForeignKey("staff_accounts.id"))
    closed_at: Mapped[int] = mapped_column(Integer, default=epoch)



class NativeSourceMedia(Base):
    """Pre-event upload: each source frame has one stable Supervisely image ID.

    Automatic jobs reference subsets of these IDs; they never upload copies.
    """

    __tablename__ = "native_source_media"
    __table_args__ = (
        UniqueConstraint("media_id", name="uq_native_source_media"),
        UniqueConstraint("native_image_id", name="uq_native_source_image"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), index=True)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("media_datasets.id"), index=True)
    media_id: Mapped[str] = mapped_column(ForeignKey("media_assets.id"), index=True)
    native_dataset_id: Mapped[int] = mapped_column(Integer)
    native_image_id: Mapped[int] = mapped_column(Integer)


class NativeMediaInstance(Base):
    """Per-job copies give overlapping teams independent annotation image identities."""

    __tablename__ = "native_media_instances"
    __table_args__ = (
        UniqueConstraint("job_id", "media_id", name="uq_native_job_media"),
        UniqueConstraint("native_image_id", name="uq_native_media_image_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("labeling_jobs.id"), index=True)
    media_id: Mapped[str] = mapped_column(ForeignKey("media_assets.id"), index=True)
    native_dataset_id: Mapped[int] = mapped_column(Integer)
    native_image_id: Mapped[int] = mapped_column(Integer)


class NativeOperation(Base):
    """Durable, deduplicated tasks for an optional Supervisely SDK worker."""

    __tablename__ = "native_operations"
    __table_args__ = (
        UniqueConstraint("kind", "target_id", name="uq_native_task_target"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    target_id: Mapped[str] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    priority: Mapped[int] = mapped_column(Integer, default=0)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(70), nullable=True)
    created_at: Mapped[int] = mapped_column(Integer, default=epoch)
    claimed_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completed_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Validation/read tasks use renewable, fenced leases and bounded retries.
    # Publication has uncertain remote-write outcomes and is NEVER auto-replayed.
    lease_owner: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_until: Mapped[int | None] = mapped_column(Integer, nullable=True)
    next_attempt_at: Mapped[int | None] = mapped_column(Integer, nullable=True)


class MonitorPairMember(Base):
    """Event-local monitor membership of a two-person competition team.

    The source monitor role remains unchanged. Disbanding only deactivates
    this relationship and keeps all historical audit records intact.
    """

    __tablename__ = "monitor_pair_members"
    __table_args__ = (
        UniqueConstraint("team_id", "person_id", name="uq_monitor_pair_team_person"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), index=True)
    team_id: Mapped[str] = mapped_column(ForeignKey("competition_teams.id"), index=True)
    person_id: Mapped[str] = mapped_column(ForeignKey("people.id"), index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[int] = mapped_column(Integer, default=epoch)


class NativeJobRevision(Base):
    """Historical native IDs after a correction restart, never overwritten."""

    __tablename__ = "native_job_revisions"
    __table_args__ = (
        UniqueConstraint("native_job_id", name="uq_native_historical_job_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("labeling_jobs.id"), index=True)
    person_id: Mapped[str] = mapped_column(ForeignKey("people.id"), index=True)
    native_job_id: Mapped[int] = mapped_column(Integer)
    correction_round: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[int] = mapped_column(Integer, default=epoch)

class ReviewRubric(Base):
    __tablename__ = "review_rubrics"
    __table_args__ = (UniqueConstraint("event_id", "version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    weights: Mapped[dict] = mapped_column(JSON)


class ReviewSample(Base):
    __tablename__ = "review_samples"
    __table_args__ = (
        UniqueConstraint("job_id", "correction_round", "media_id",
                         name="uq_review_sample_job_round_media"),
        Index("ix_review_sample_claim", "event_id", "team_id", "random_key"),
        Index(
            "ix_review_sample_event_random_pending", "event_id", "random_key",
            postgresql_where=text("completed_at IS NULL"),
            sqlite_where=text("completed_at IS NULL"),
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("labeling_jobs.id"))
    team_id: Mapped[str] = mapped_column(ForeignKey("competition_teams.id"))
    media_id: Mapped[str] = mapped_column(ForeignKey("media_assets.id"))
    correction_round: Mapped[int] = mapped_column(Integer)
    validated_complete: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    random_key: Mapped[str] = mapped_column(String(36), default=identifier)
    snapshot: Mapped[dict] = mapped_column(JSON)
    reviewer_id: Mapped[str | None] = mapped_column(ForeignKey("staff_accounts.id"))
    claim_token: Mapped[str | None] = mapped_column(String(36))
    expires_at: Mapped[int | None] = mapped_column(Integer)
    completed_at: Mapped[int | None] = mapped_column(Integer)
    rubric_id: Mapped[str | None] = mapped_column(ForeignKey("review_rubrics.id"))
    decision: Mapped[dict | None] = mapped_column(JSON)
