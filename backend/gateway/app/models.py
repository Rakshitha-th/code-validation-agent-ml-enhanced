from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class UserRecord(Base):
    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="Developer")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sessions: Mapped[list["AuthSessionRecord"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    projects: Mapped[list["ProjectRecord"]] = relationship(back_populates="owner")


class AuthSessionRecord(Base):
    __tablename__ = "auth_sessions"

    session_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    user: Mapped[UserRecord] = relationship(back_populates="sessions")


class PasswordResetTokenRecord(Base):
    __tablename__ = "password_reset_tokens"

    reset_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProjectRecord(Base):
    __tablename__ = "projects"

    project_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str | None] = mapped_column(ForeignKey("users.user_id", ondelete="SET NULL"), index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="uploaded")
    file_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    files: Mapped[list["ProjectFileRecord"]] = relationship(
        back_populates="project", cascade="all, delete-orphan", passive_deletes=True
    )
    runs: Mapped[list["AnalysisRunRecord"]] = relationship(
        back_populates="project", cascade="all, delete-orphan", passive_deletes=True
    )
    owner: Mapped[UserRecord | None] = relationship(back_populates="projects")


class ProjectFileRecord(Base):
    __tablename__ = "project_files"

    project_id: Mapped[str] = mapped_column(ForeignKey("projects.project_id", ondelete="CASCADE"), primary_key=True)
    path: Mapped[str] = mapped_column(String(512), primary_key=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    project: Mapped[ProjectRecord] = relationship(back_populates="files")


class AnalysisRunRecord(Base):
    __tablename__ = "analysis_runs"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.project_id", ondelete="CASCADE"), index=True)
    workflow_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    progress: Mapped[str | None] = mapped_column(String(80))
    validation_status: Mapped[str | None] = mapped_column(String(32))
    tools: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    project_context: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict | None] = mapped_column(JSON)
    project: Mapped[ProjectRecord] = relationship(back_populates="runs")
    findings: Mapped[list["FindingRecord"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", passive_deletes=True
    )
    tool_results: Mapped[list["ToolResultRecord"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", passive_deletes=True
    )
    events: Mapped[list["WorkflowEventRecord"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", passive_deletes=True
    )
    repairs: Mapped[list["RepairRecord"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", passive_deletes=True
    )
    validation: Mapped["ValidationRecord | None"] = relationship(
        back_populates="run", cascade="all, delete-orphan", passive_deletes=True, uselist=False
    )
    report: Mapped["ReportRecord | None"] = relationship(
        back_populates="run", cascade="all, delete-orphan", passive_deletes=True, uselist=False
    )


class FindingRecord(Base):
    __tablename__ = "findings"

    run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.run_id", ondelete="CASCADE"), primary_key=True)
    finding_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    agent: Mapped[str | None] = mapped_column(String(80))
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    severity: Mapped[str] = mapped_column(String(24), nullable=False)
    file_path: Mapped[str | None] = mapped_column(String(512))
    line_number: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open")
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    run: Mapped[AnalysisRunRecord] = relationship(back_populates="findings")


class ToolResultRecord(Base):
    __tablename__ = "tool_results"

    run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.run_id", ondelete="CASCADE"), primary_key=True)
    tool_name: Mapped[str] = mapped_column(String(80), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    duration_ms: Mapped[float | None]
    error: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    run: Mapped[AnalysisRunRecord] = relationship(back_populates="tool_results")


class WorkflowEventRecord(Base):
    __tablename__ = "workflow_events"

    event_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.run_id", ondelete="CASCADE"), index=True)
    event: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str | None] = mapped_column(String(32))
    message: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    run: Mapped[AnalysisRunRecord] = relationship(back_populates="events")


class RepairRecord(Base):
    __tablename__ = "repairs"

    run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.run_id", ondelete="CASCADE"), primary_key=True)
    iteration: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    run: Mapped[AnalysisRunRecord] = relationship(back_populates="repairs")


class ValidationRecord(Base):
    __tablename__ = "validation_results"

    run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.run_id", ondelete="CASCADE"), primary_key=True)
    passed: Mapped[bool | None] = mapped_column(Boolean)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    run: Mapped[AnalysisRunRecord] = relationship(back_populates="validation")


class ReportRecord(Base):
    __tablename__ = "reports"

    run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.run_id", ondelete="CASCADE"), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    run: Mapped[AnalysisRunRecord] = relationship(back_populates="report")