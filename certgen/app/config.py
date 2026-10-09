"""Application settings, read from environment variables with sane defaults."""
import os
from dataclasses import dataclass, field
from pathlib import Path


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


@dataclass
class Settings:
    database_url: str = field(
        default_factory=lambda: os.environ.get("DATABASE_URL", "sqlite:///./certgen.db")
    )
    storage_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("STORAGE_DIR", "./storage"))
    )
    # "background": jobs run in a worker thread pool, POST returns 202 immediately.
    # "inline": jobs run inside the request (used by tests / debugging).
    processing_mode: str = field(
        default_factory=lambda: os.environ.get("PROCESSING_MODE", "background")
    )
    worker_threads: int = field(default_factory=lambda: _env_int("WORKER_THREADS", 2))
    max_recipients_per_job: int = field(
        default_factory=lambda: _env_int("MAX_RECIPIENTS_PER_JOB", 5000)
    )
    # Optional TTF file for non-Latin names. Without it only Latin-1 text is supported.
    font_path: str | None = field(default_factory=lambda: os.environ.get("CERT_FONT_PATH"))
