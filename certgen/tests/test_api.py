import io
import time
import zipfile

from app.config import Settings
from app.generator import CertificateGenerator
from app.main import create_app

from .conftest import payload, people


# ---------- creating a job ----------
def test_create_job_returns_202_and_job_summary(client):
    r = client.post("/jobs", json=payload(people(3), issue_date="2026-01-15"))
    assert r.status_code == 202
    body = r.json()
    assert body["total"] == 3
    assert body["issue_date"] == "2026-01-15"
    assert body["id"] and body["certificates_url"] == f"/jobs/{body['id']}/certificates"


def test_unknown_job_is_404(client):
    assert client.get("/jobs/nope").status_code == 404
    assert client.get("/jobs/nope/certificates").status_code == 404
    assert client.get("/jobs/nope/download").status_code == 404
    assert client.get("/certificates/nope/download").status_code == 404


# ---------- input validation ----------
def test_request_level_validation(client):
    assert client.post("/jobs", json=payload([])).status_code == 422          # empty list
    assert client.post("/jobs", json={"recipients": people(1)}).status_code == 422  # missing course
    assert client.post("/jobs", json=payload(people(1), course_name="  ")).status_code == 422
    assert client.post("/jobs", json=payload(people(1), issue_date="not-a-date")).status_code == 422
    assert client.post("/jobs", json=payload("nope")).status_code == 422


def test_too_many_recipients_rejected(client):
    r = client.post("/jobs", json=payload(people(51)))  # limit is 50 in tests
    assert r.status_code == 422 and "Too many" in r.json()["detail"]


def test_invalid_recipients_are_reported_per_row_and_valid_ones_still_generate(client):
    recipients = [
        {"name": "Good One", "email": "good@example.com"},
        {"name": "", "email": "x@example.com"},
        {"name": "No Email"},
        {"name": "Bad Email", "email": "not-an-email"},
        "just a string",
        {"name": "Good Two", "email": "good2@example.com"},
        {"name": "Dup", "email": "GOOD@example.com"},          # duplicate (case-insensitive)
        {"name": "Ünïcode ✓", "email": "u@example.com"},      # '✓' not renderable with built-in fonts
    ]
    job = client.post("/jobs", json=payload(recipients)).json()
    detail = client.get(f"/jobs/{job['id']}").json()

    assert detail["status"] == "completed_with_errors"
    assert (detail["succeeded"], detail["failed"], detail["pending"]) == (2, 6, 0)
    failed = {f["position"]: f["error"] for f in detail["failures"]}
    assert set(failed) == {1, 2, 3, 4, 6, 7}
    assert "name is required" in failed[1]
    assert "email is required" in failed[2]
    assert "invalid email" in failed[3]
    assert "must be an object" in failed[4]
    assert "duplicate" in failed[6]
    assert "cannot render" in failed[7]


def test_all_invalid_job_fails_immediately(client):
    job = client.post("/jobs", json=payload([{"name": "x"}, {}])).json()
    assert job["status"] == "failed"
    assert job["completed_at"] is not None
    assert client.get(f"/jobs/{job['id']}/download").status_code == 409


# ---------- certificate generation ----------
def test_certificates_are_valid_pdfs_with_recipient_data(client, settings):
    job = client.post("/jobs", json=payload(people(2), course_name="Data Science 101")).json()
    certs = client.get(job["certificates_url"]).json()["items"]
    assert [c["status"] for c in certs] == ["succeeded", "succeeded"]

    r = client.get(certs[0]["download_url"])
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF") and r.content.rstrip().endswith(b"%%EOF")
    assert 'filename="0001_Person_0.pdf"' in r.headers["content-disposition"]

    # Files exist on disk, one per certificate, under the job folder.
    assert len(list((settings.storage_dir / job["id"]).glob("*.pdf"))) == 2


def test_pdf_contains_the_recipient_specific_text(client, settings):
    """Reportlab compresses streams; disable that to assert on text content."""
    import reportlab.rl_config as rl
    rl.pageCompression = 0
    try:
        job = client.post("/jobs", json=payload([{"name": "Zainab Qureshi", "email": "z@example.com"}],
                                                 course_name="Quantum Basket Weaving")).json()
        cert = client.get(job["certificates_url"]).json()["items"][0]
        pdf = client.get(cert["download_url"]).content
        assert b"Zainab Qureshi" in pdf and b"Quantum Basket Weaving" in pdf and b"Acme Academy" in pdf
    finally:
        rl.pageCompression = 1


def test_very_long_name_still_generates(client):
    long_name = "A" * 99
    job = client.post("/jobs", json=payload([{"name": long_name, "email": "l@example.com"}])).json()
    assert job["status"] == "completed"


# ---------- status / progress ----------
def test_status_progress_while_running_and_after(settings):
    """Run in background mode with a slow generator and watch the job progress."""
    settings.processing_mode = "background"

    class Slow(CertificateGenerator):
        def render(self, *a, **kw):
            time.sleep(0.05)
            super().render(*a, **kw)

    from fastapi.testclient import TestClient
    with TestClient(create_app(settings, Slow())) as c:
        job = c.post("/jobs", json=payload(people(10))).json()
        assert job["status"] in ("pending", "processing")   # POST did not wait for the work
        assert job["total"] == 10

        seen_partial = False
        deadline = time.time() + 20
        while time.time() < deadline:
            d = c.get(f"/jobs/{job['id']}").json()
            if 0 < d["succeeded"] < 10:
                seen_partial = True
                assert d["status"] == "processing"
                assert 0 < d["progress_percent"] < 100
                assert d["succeeded"] + d["failed"] + d["pending"] == 10
            if d["status"] == "completed":
                break
            time.sleep(0.02)
        assert d["status"] == "completed"
        assert d["progress_percent"] == 100.0 and d["succeeded"] == 10
        assert d["started_at"] and d["completed_at"]
        assert seen_partial, "never observed intermediate progress"


def test_list_jobs_with_pagination_and_status_filter(client):
    for _ in range(3):
        client.post("/jobs", json=payload(people(1)))
    client.post("/jobs", json=payload([{"name": "bad"}]))
    page = client.get("/jobs?limit=2").json()
    assert page["total"] == 4 and len(page["items"]) == 2
    assert client.get("/jobs?status=failed").json()["total"] == 1
    assert client.get("/jobs?limit=0").status_code == 422


# ---------- individual certificate failure ----------
class FailsForName(CertificateGenerator):
    def render(self, out_path, *, recipient_name, **kw):
        if recipient_name == "Person 2":
            raise RuntimeError("boom")
        super().render(out_path, recipient_name=recipient_name, **kw)


def test_one_generation_failure_does_not_stop_the_others(make_client, settings):
    client = make_client(FailsForName())
    job = client.post("/jobs", json=payload(people(5))).json()
    d = client.get(f"/jobs/{job['id']}").json()

    assert d["status"] == "completed_with_errors"
    assert (d["succeeded"], d["failed"]) == (4, 1)
    assert d["failures"][0]["position"] == 2
    assert "RuntimeError: boom" in d["failures"][0]["error"]

    items = client.get(f"/jobs/{job['id']}/certificates").json()["items"]
    assert [c["status"] for c in items] == ["succeeded", "succeeded", "failed", "succeeded", "succeeded"]
    assert items[2]["download_url"] is None
    # no partial/temp files left behind for the failed one
    assert len(list((settings.storage_dir / job["id"]).iterdir())) == 4

    r = client.get(f"/certificates/{items[2]['id']}/download")
    assert r.status_code == 409 and "boom" in r.json()["detail"]


def test_all_generation_failures_marks_job_failed(make_client):
    class AlwaysFails(CertificateGenerator):
        def render(self, *a, **kw):
            raise OSError("disk full")

    client = make_client(AlwaysFails())
    job = client.post("/jobs", json=payload(people(2))).json()
    assert client.get(f"/jobs/{job['id']}").json()["status"] == "failed"


# ---------- retrieving certificates ----------
def test_list_filter_and_paginate_certificates(make_client):
    client = make_client(FailsForName())
    job = client.post("/jobs", json=payload(people(5))).json()
    base = f"/jobs/{job['id']}/certificates"
    assert client.get(base + "?status=failed").json()["total"] == 1
    page = client.get(base + "?limit=2&offset=2").json()
    assert page["total"] == 5 and [c["position"] for c in page["items"]] == [2, 3]
    assert client.get(base + "?status=bogus").status_code == 422


def test_download_all_as_zip_includes_only_successes(make_client):
    client = make_client(FailsForName())
    job = client.post("/jobs", json=payload(people(4))).json()
    r = client.get(job["download_all_url"] if job["download_all_url"] else f"/jobs/{job['id']}/download")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert names == ["0001_Person_0.pdf", "0002_Person_1.pdf", "0004_Person_3.pdf"]


def test_download_missing_file_returns_410(client, settings):
    job = client.post("/jobs", json=payload(people(1))).json()
    cert = client.get(job["certificates_url"]).json()["items"][0]
    next((settings.storage_dir / job["id"]).glob("*.pdf")).unlink()
    assert client.get(cert["download_url"]).status_code == 410


# ---------- restart recovery ----------
def test_interrupted_job_is_resumed_on_startup(settings):
    from fastapi.testclient import TestClient
    from sqlalchemy import update
    from app.models import Job

    with TestClient(create_app(settings)) as c:
        job = c.post("/jobs", json=payload(people(3))).json()
        sf = c.app.state.session_factory
    # Simulate a crash: job stuck in 'processing' with its work not done.
    with sf() as db:
        from app.models import Certificate
        db.execute(update(Certificate).where(Certificate.job_id == job["id"]).values(status="pending", file_path=None))
        db.execute(update(Job).where(Job.id == job["id"]).values(status="processing"))
        db.commit()
    with TestClient(create_app(settings)) as c2:   # "restart"
        d = c2.get(f"/jobs/{job['id']}").json()
        assert d["status"] == "completed" and d["succeeded"] == 3
