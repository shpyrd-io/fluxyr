"""Single-agent persistence schema. All times are UTC Unix timestamps."""

import time
import uuid

from sqlalchemy import (
    JSON,
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def new_id():
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class Version(Base):
    __tablename__ = "schema_version"
    id: Mapped[int] = mapped_column(primary_key=True)


class Session(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(255), default="Execution")
    kind: Mapped[str] = mapped_column(String(20), default="execution")
    status: Mapped[str] = mapped_column(String(24), default="idle", index=True)
    brain: Mapped[dict | None] = mapped_column(JSON)
    memories: Mapped[dict | None] = mapped_column(JSON)
    memory_epoch: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), index=True)
    job_id: Mapped[str | None] = mapped_column(String(36), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_routine_status_owner", "routine_id", "status", "owner"),
        Index("ix_jobs_session_status_created", "session_id", "status", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), index=True)
    routine_id: Mapped[str | None] = mapped_column(String(36), index=True)
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    prompt: Mapped[str] = mapped_column(Text)
    input: Mapped[dict] = mapped_column(JSON, default=dict)
    snapshot: Mapped[dict | None] = mapped_column(JSON)
    brain: Mapped[dict | None] = mapped_column(JSON)
    outcome: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    control: Mapped[str | None] = mapped_column(String(16))
    owner: Mapped[str | None] = mapped_column(String(64))
    lease_until: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    started_at: Mapped[float | None] = mapped_column(Float)
    finished_at: Mapped[float | None] = mapped_column(Float)
    schedule_key: Mapped[str | None] = mapped_column(String(100), unique=True)


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_type_id", "type", "id"),
        Index("ix_events_session_id_id", "session_id", "id"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), index=True)
    job_id: Mapped[str | None] = mapped_column(String(36), index=True)
    type: Mapped[str] = mapped_column(String(60))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class Decision(Base):
    __tablename__ = "decisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    call_id: Mapped[str] = mapped_column(String(255))
    value: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    __table_args__ = (UniqueConstraint("job_id", "call_id"),)


class Effect(Base):
    __tablename__ = "effects"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    tool_name: Mapped[str] = mapped_column(String(255))
    occurrence: Mapped[int] = mapped_column(Integer)
    fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="claimed")
    result: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    __table_args__ = (UniqueConstraint("job_id", "tool_name", "occurrence"),)


class Skill(Base):
    __tablename__ = "skills"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    instruction: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    deleted_at: Mapped[float | None] = mapped_column(Float)
    spec: Mapped[str] = mapped_column(Text, default="", server_default="")
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class Tool(Base):
    __tablename__ = "tools"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    skill_id: Mapped[str] = mapped_column(ForeignKey("skills.id"), index=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    description: Mapped[str] = mapped_column(Text)
    active_version: Mapped[str | None] = mapped_column(String(36))


class ToolVersion(Base):
    __tablename__ = "tool_versions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tool_id: Mapped[str] = mapped_column(ForeignKey("tools.id"), index=True)
    source: Mapped[str] = mapped_column(Text)
    parameters: Mapped[dict] = mapped_column(JSON, default=dict)
    dependencies: Mapped[list] = mapped_column(JSON, default=list)
    secrets: Mapped[list] = mapped_column(JSON, default=list)
    test_result: Mapped[dict | None] = mapped_column(JSON)
    environment: Mapped[str | None] = mapped_column(String(80))
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=True)
    state: Mapped[str] = mapped_column(String(24), default="candidate")
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class VaultItem(Base):
    __tablename__ = "vault_items"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    type: Mapped[str] = mapped_column(String(30))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class OAuthState(Base):
    __tablename__ = "oauth_states"
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    vault_id: Mapped[str] = mapped_column(ForeignKey("vault_items.id"))
    payload: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[float] = mapped_column(Float)


class Routine(Base):
    __tablename__ = "routines"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    prompt: Mapped[str] = mapped_column(Text)
    trigger: Mapped[str] = mapped_column(
        String(16), default="manual", server_default="manual"
    )
    expectation: Mapped[str] = mapped_column(Text)
    output_schema: Mapped[dict | None] = mapped_column(JSON)
    checks: Mapped[list] = mapped_column(JSON, default=list)
    cron: Mapped[str | None] = mapped_column(String(100))
    timezone: Mapped[str] = mapped_column(String(100), default="UTC")
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    next_run: Mapped[float | None] = mapped_column(Float, index=True)
    overlap: Mapped[str] = mapped_column(String(16), default="queue")
    max_concurrency: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class Configuration(Base):
    __tablename__ = "configuration"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)


class MemoryTask(Base):
    __tablename__ = "memory_tasks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), unique=True)
    epoch: Mapped[int] = mapped_column(Integer)
    messages: Mapped[list] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    result: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[float] = mapped_column(Float, default=time.time)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    finished_at: Mapped[float | None] = mapped_column(Float)


class ReactiveConfig(Base):
    __tablename__ = "reactive_configs"
    routine_id: Mapped[str] = mapped_column(
        ForeignKey("routines.id", ondelete="CASCADE"), primary_key=True
    )
    mode: Mapped[str] = mapped_column(String(16), default="collecting")
    token: Mapped[str] = mapped_column(String(100), unique=True)
    signing_secret: Mapped[str | None] = mapped_column(Text)
    normalizer_id: Mapped[str | None] = mapped_column(String(36))


class NormalizerVersion(Base):
    __tablename__ = "normalizer_versions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    routine_id: Mapped[str] = mapped_column(
        ForeignKey("routines.id", ondelete="CASCADE"), index=True
    )
    source: Mapped[str] = mapped_column(Text)
    dependencies: Mapped[list] = mapped_column(JSON, default=list)
    tested_at: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class IncomingReceipt(Base):
    __tablename__ = "incoming_receipts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    routine_id: Mapped[str] = mapped_column(
        ForeignKey("routines.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(24), index=True)
    envelope: Mapped[dict] = mapped_column(JSON)
    normalizer_id: Mapped[str | None] = mapped_column(String(36))
    prompt: Mapped[str] = mapped_column(Text)
    transport_key: Mapped[str | None] = mapped_column(String(64))
    result: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    __table_args__ = (
        UniqueConstraint("routine_id", "transport_key"),
        {"sqlite_autoincrement": True},
    )


class NormalizationAttempt(Base):
    __tablename__ = "normalization_attempts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    receipt_id: Mapped[int] = mapped_column(
        ForeignKey("incoming_receipts.id", ondelete="CASCADE"), index=True
    )
    version_id: Mapped[str] = mapped_column(
        ForeignKey("normalizer_versions.id", ondelete="CASCADE"), index=True
    )
    dry_run: Mapped[bool] = mapped_column(Boolean)
    status: Mapped[str] = mapped_column(String(24), default="running")
    result: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    finished_at: Mapped[float | None] = mapped_column(Float)


class ReactiveSession(Base):
    __tablename__ = "reactive_sessions"
    routine_id: Mapped[str] = mapped_column(
        ForeignKey("routines.id", ondelete="CASCADE"), primary_key=True
    )
    session_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"))


class ReactiveDelivery(Base):
    __tablename__ = "reactive_deliveries"
    routine_id: Mapped[str] = mapped_column(
        ForeignKey("routines.id", ondelete="CASCADE"), primary_key=True
    )
    event_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"))
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
