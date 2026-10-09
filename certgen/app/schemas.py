from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator


class JobCreate(BaseModel):
    course_name: str = Field(min_length=1, max_length=200, examples=["Python Bootcamp 2026"])
    issuer_name: str = Field(min_length=1, max_length=200, examples=["Acme Academy"])
    issue_date: date | None = Field(default=None, description="Defaults to today (UTC)")
    # Items are validated individually (see validation.py) so that one bad row
    # does not reject the whole request; hence the loose `Any` here.
    recipients: list[Any] = Field(min_length=1, examples=[[{"name": "Asha Rao", "email": "asha@example.com"}]])

    @field_validator("course_name", "issuer_name")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v:
            raise ValueError("must not be blank")
        return v


class CertificateOut(BaseModel):
    id: str
    position: int
    recipient_name: str | None
    recipient_email: str | None
    status: str
    error: str | None
    generated_at: datetime | None
    download_url: str | None


class CertificatePage(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[CertificateOut]


class JobOut(BaseModel):
    id: str
    course_name: str
    issuer_name: str
    issue_date: date
    status: str
    total: int
    succeeded: int
    failed: int
    pending: int
    progress_percent: float
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    certificates_url: str
    download_all_url: str | None


class JobDetail(JobOut):
    # First failures inline, so a client can see what went wrong without a second call.
    failures: list[CertificateOut]
    failures_truncated: bool


class JobPage(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[JobOut]
