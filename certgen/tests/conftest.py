import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.generator import CertificateGenerator
from app.main import create_app


@pytest.fixture
def settings(tmp_path):
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        storage_dir=tmp_path / "storage",
        processing_mode="inline",  # deterministic: job is finished when POST returns
        max_recipients_per_job=50,
    )


@pytest.fixture
def make_client(settings):
    clients = []

    def _make(generator: CertificateGenerator | None = None, **overrides) -> TestClient:
        for k, v in overrides.items():
            setattr(settings, k, v)
        c = TestClient(create_app(settings, generator))
        c.__enter__()
        clients.append(c)
        return c

    yield _make
    for c in clients:
        c.__exit__(None, None, None)


@pytest.fixture
def client(make_client):
    return make_client()


def payload(recipients, **kw):
    body = {"course_name": "Python Bootcamp", "issuer_name": "Acme Academy", "recipients": recipients}
    body.update(kw)
    return body


def people(n):
    return [{"name": f"Person {i}", "email": f"p{i}@example.com"} for i in range(n)]
