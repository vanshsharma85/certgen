import re
import tempfile
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import service
from .config import Settings
from .database import make_session_factory
from .generator import CertificateGenerator
from .models import Certificate, CertStatus, Job, JobStatus
from .schemas import CertificateOut, CertificatePage, JobCreate, JobDetail, JobOut, JobPage

MAX_INLINE_FAILURES = 100
TERMINAL = {JobStatus.COMPLETED.value, JobStatus.COMPLETED_WITH_ERRORS.value, JobStatus.FAILED.value}


def create_app(settings: Settings | None = None, generator: CertificateGenerator | None = None) -> FastAPI:
    settings = settings or Settings()
    settings.storage_dir = Path(settings.storage_dir)
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    engine, session_factory = make_session_factory(settings.database_url)
    generator = generator or CertificateGenerator(settings.font_path)
    executor = ThreadPoolExecutor(max_workers=settings.worker_threads, thread_name_prefix="certgen")

    def enqueue(job_id: str) -> None:
        args = (session_factory, app.state.generator, settings.storage_dir, job_id)
        if settings.processing_mode == "inline":
            service.process_job(*args)
        else:
            executor.submit(service.process_job, *args)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        for job_id in service.recover_unfinished(session_factory):
            enqueue(job_id)
        yield
        executor.shutdown(wait=True, cancel_futures=False)
        engine.dispose()

    app = FastAPI(title="Bulk Certificate Generator", version="1.0.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.generator = generator
    app.state.session_factory = session_factory

    def get_db():
        with session_factory() as db:
            yield db

    # ---------- helpers ----------
    def cert_out(c: Certificate) -> CertificateOut:
        ok = c.status == CertStatus.SUCCEEDED.value
        return CertificateOut(
            id=c.id, position=c.position, recipient_name=c.recipient_name,
            recipient_email=c.recipient_email, status=c.status, error=c.error,
            generated_at=c.generated_at,
            download_url=f"/certificates/{c.id}/download" if ok else None,
        )

    def job_out(db: Session, job: Job) -> dict:
        c = service.counts(db, job.id)
        done = c["succeeded"] + c["failed"]
        return dict(
            id=job.id, course_name=job.course_name, issuer_name=job.issuer_name,
            issue_date=job.issue_date, status=job.status, total=job.total,
            succeeded=c["succeeded"], failed=c["failed"], pending=c["pending"],
            progress_percent=round(100 * done / job.total, 1) if job.total else 100.0,
            created_at=job.created_at, started_at=job.started_at, completed_at=job.completed_at,
            certificates_url=f"/jobs/{job.id}/certificates",
            download_all_url=f"/jobs/{job.id}/download" if c["succeeded"] else None,
        )

    def get_job_or_404(db: Session, job_id: str) -> Job:
        job = db.get(Job, job_id)
        if not job:
            raise HTTPException(404, "Job not found")
        return job

    # ---------- routes ----------
    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/jobs", response_model=JobOut, status_code=202)
    def create_job(body: JobCreate, db: Session = Depends(get_db)):
        """Submit a bulk request. Returns immediately with a job to poll."""
        if len(body.recipients) > settings.max_recipients_per_job:
            raise HTTPException(
                422, f"Too many recipients: {len(body.recipients)} (max {settings.max_recipients_per_job} per job)"
            )
        job = service.create_job(
            db, course_name=body.course_name, issuer_name=body.issuer_name,
            issue_date=body.issue_date, recipients=body.recipients,
            unicode_font=bool(settings.font_path),
        )
        if job.status == JobStatus.PENDING.value:
            enqueue(job.id)
            db.refresh(job)
        return job_out(db, job)

    @app.get("/jobs", response_model=JobPage)
    def list_jobs(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
                  status: JobStatus | None = None, db: Session = Depends(get_db)):
        q = select(Job)
        if status:
            q = q.where(Job.status == status.value)
        total = db.scalar(select(func.count()).select_from(q.subquery()))
        jobs = db.scalars(q.order_by(Job.created_at.desc()).limit(limit).offset(offset)).all()
        return JobPage(total=total, limit=limit, offset=offset, items=[job_out(db, j) for j in jobs])

    @app.get("/jobs/{job_id}", response_model=JobDetail)
    def get_job(job_id: str, db: Session = Depends(get_db)):
        """Status + progress counts + first failures (with reasons)."""
        job = get_job_or_404(db, job_id)
        data = job_out(db, job)
        failed = db.scalars(
            select(Certificate)
            .where(Certificate.job_id == job_id, Certificate.status == CertStatus.FAILED.value)
            .order_by(Certificate.position).limit(MAX_INLINE_FAILURES)
        ).all()
        return JobDetail(**data, failures=[cert_out(c) for c in failed],
                         failures_truncated=data["failed"] > MAX_INLINE_FAILURES)

    @app.get("/jobs/{job_id}/certificates", response_model=CertificatePage)
    def list_certificates(job_id: str, status: CertStatus | None = None,
                          limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0),
                          db: Session = Depends(get_db)):
        get_job_or_404(db, job_id)
        q = select(Certificate).where(Certificate.job_id == job_id)
        if status:
            q = q.where(Certificate.status == status.value)
        total = db.scalar(select(func.count()).select_from(q.subquery()))
        rows = db.scalars(q.order_by(Certificate.position).limit(limit).offset(offset)).all()
        return CertificatePage(total=total, limit=limit, offset=offset, items=[cert_out(c) for c in rows])

    @app.get("/certificates/{cert_id}/download")
    def download_certificate(cert_id: str, db: Session = Depends(get_db)):
        cert = db.get(Certificate, cert_id)
        if not cert:
            raise HTTPException(404, "Certificate not found")
        if cert.status != CertStatus.SUCCEEDED.value:
            raise HTTPException(409, f"Certificate is {cert.status}" + (f": {cert.error}" if cert.error else ""))
        path = settings.storage_dir / cert.file_path
        if not path.is_file():
            raise HTTPException(410, "Certificate file is no longer available")
        return FileResponse(path, media_type="application/pdf", filename=_filename(cert))

    @app.get("/jobs/{job_id}/download")
    def download_job_zip(job_id: str, db: Session = Depends(get_db)):
        """ZIP with every successfully generated certificate (works on partial jobs too)."""
        job = get_job_or_404(db, job_id)
        certs = db.scalars(
            select(Certificate)
            .where(Certificate.job_id == job_id, Certificate.status == CertStatus.SUCCEEDED.value)
            .order_by(Certificate.position)
        ).all()
        if not certs:
            raise HTTPException(409, "No certificates have been generated for this job yet")
        tmp = tempfile.SpooledTemporaryFile(max_size=32 * 1024 * 1024)
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            for c in certs:
                path = settings.storage_dir / c.file_path
                if path.is_file():
                    zf.write(path, arcname=_filename(c))
        tmp.seek(0)

        def stream():
            try:
                while chunk := tmp.read(1024 * 256):
                    yield chunk
            finally:
                tmp.close()

        return StreamingResponse(
            stream(), media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="certificates-{job.id}.zip"'},
        )

    return app


def _filename(cert: Certificate) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", cert.recipient_name or "certificate").strip("_") or "certificate"
    return f"{cert.position + 1:04d}_{safe}.pdf"  # position prefix keeps names unique
