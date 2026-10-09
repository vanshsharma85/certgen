"""Job creation and processing logic (framework-independent)."""
import logging
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from .generator import CertificateGenerator
from .models import Certificate, CertStatus, Job, JobStatus
from .validation import validate_recipient

log = logging.getLogger("certgen")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_job(
    db: Session,
    *,
    course_name: str,
    issuer_name: str,
    issue_date: date | None,
    recipients: list,
    unicode_font: bool = False,
) -> Job:
    """Validate every recipient and persist the job + one Certificate row per recipient.

    Invalid or duplicate recipients are stored as FAILED rows (with the reason) instead
    of rejecting the request, so the client gets a complete per-row report.
    """
    job = Job(
        course_name=course_name,
        issuer_name=issuer_name,
        issue_date=issue_date or _now().date(),
        total=len(recipients),
    )
    seen_emails: dict[str, int] = {}
    certs: list[Certificate] = []
    for pos, raw in enumerate(recipients):
        clean, error = validate_recipient(raw, unicode_font=unicode_font)
        if clean and clean["email"].lower() in seen_emails:
            first = seen_emails[clean["email"].lower()]
            error = f"duplicate email (already used by recipient at position {first})"
        elif clean:
            seen_emails[clean["email"].lower()] = pos

        cert = Certificate(
            position=pos,
            raw_input=raw if isinstance(raw, dict) else {"value": repr(raw)[:200]},
            recipient_name=(clean or {}).get("name") or _safe_str(raw, "name"),
            recipient_email=(clean or {}).get("email") or _safe_str(raw, "email"),
            status=CertStatus.FAILED.value if error else CertStatus.PENDING.value,
            error=error,
        )
        certs.append(cert)

    job.certificates = certs
    if all(c.status == CertStatus.FAILED.value for c in certs):
        job.status = JobStatus.FAILED.value  # nothing to generate
        job.completed_at = _now()
    db.add(job)
    db.commit()
    return job


def _safe_str(raw, key: str) -> str | None:
    if isinstance(raw, dict) and isinstance(raw.get(key), str):
        return raw[key][:300]
    return None


def counts(db: Session, job_id: str) -> dict[str, int]:
    rows = db.execute(
        select(Certificate.status, func.count()).where(Certificate.job_id == job_id).group_by(Certificate.status)
    ).all()
    out = {s.value: 0 for s in CertStatus}
    out.update({status: n for status, n in rows})
    return out


def _final_status(c: dict[str, int]) -> str:
    if c[CertStatus.SUCCEEDED.value] == 0:
        return JobStatus.FAILED.value
    if c[CertStatus.FAILED.value] == 0:
        return JobStatus.COMPLETED.value
    return JobStatus.COMPLETED_WITH_ERRORS.value


def process_job(
    session_factory: sessionmaker,
    generator: CertificateGenerator,
    storage_dir: Path,
    job_id: str,
) -> None:
    """Generate all pending certificates of a job. Never raises for per-item errors."""
    with session_factory() as db:
        # Atomically claim the job so it can't be processed twice.
        claimed = db.execute(
            update(Job)
            .where(Job.id == job_id, Job.status == JobStatus.PENDING.value)
            .values(status=JobStatus.PROCESSING.value, started_at=_now())
        ).rowcount
        db.commit()
        if not claimed:
            return

        job = db.get(Job, job_id)
        pending_ids = db.scalars(
            select(Certificate.id)
            .where(Certificate.job_id == job_id, Certificate.status == CertStatus.PENDING.value)
            .order_by(Certificate.position)
        ).all()

        for cert_id in pending_ids:
            cert = db.get(Certificate, cert_id)
            rel_path = f"{job_id}/{cert.id}.pdf"
            try:
                generator.render(
                    storage_dir / rel_path,
                    recipient_name=cert.recipient_name,
                    course_name=job.course_name,
                    issuer_name=job.issuer_name,
                    issue_date=job.issue_date,
                    certificate_id=cert.id,
                )
                cert.status = CertStatus.SUCCEEDED.value
                cert.file_path = rel_path
                cert.generated_at = _now()
            except Exception as exc:  # isolate: one bad certificate must not stop the job
                log.exception("certificate %s failed", cert.id)
                cert.status = CertStatus.FAILED.value
                cert.error = f"generation failed: {type(exc).__name__}: {exc}"[:1000]
            db.commit()  # commit per certificate => progress is visible while running

        job.status = _final_status(counts(db, job_id))
        job.completed_at = _now()
        db.commit()


def recover_unfinished(session_factory: sessionmaker) -> list[str]:
    """After a restart, put interrupted jobs back to PENDING and return ids to re-enqueue.

    Already-finished certificates are kept; process_job only touches PENDING ones.
    Assumes a single app process (see README for the multi-process story).
    """
    with session_factory() as db:
        db.execute(
            update(Job).where(Job.status == JobStatus.PROCESSING.value).values(status=JobStatus.PENDING.value)
        )
        db.commit()
        return list(
            db.scalars(select(Job.id).where(Job.status == JobStatus.PENDING.value).order_by(Job.created_at))
        )
