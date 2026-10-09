import enum
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class JobStatus(str, enum.Enum):
    PENDING = "pending"                        # accepted, not started
    PROCESSING = "processing"                  # worker is generating
    COMPLETED = "completed"                    # every certificate succeeded
    COMPLETED_WITH_ERRORS = "completed_with_errors"  # some succeeded, some failed
    FAILED = "failed"                          # nothing succeeded


class CertStatus(str, enum.Enum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    course_name: Mapped[str] = mapped_column(String(200))
    issuer_name: Mapped[str] = mapped_column(String(200))
    issue_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(32), default=JobStatus.PENDING.value, index=True)
    total: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    certificates: Mapped[list["Certificate"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="Certificate.position"
    )


class Certificate(Base):
    __tablename__ = "certificates"
    __table_args__ = (Index("ix_cert_job_status", "job_id", "status"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer)  # index in the submitted list (0-based)
    recipient_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    recipient_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    raw_input: Mapped[dict] = mapped_column(JSON)  # exactly what the client sent
    status: Mapped[str] = mapped_column(String(16), default=CertStatus.PENDING.value)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_path: Mapped[str | None] = mapped_column(String(500), nullable=True)  # relative to storage dir
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    job: Mapped[Job] = relationship(back_populates="certificates")
