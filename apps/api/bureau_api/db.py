"""Database wiring — SPEC §17.

Postgres in `docker compose`, SQLite everywhere else. The fallback is not a shortcut: the
control plane has to be runnable and testable without a database server, and one URL is
the whole difference.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine

DEFAULT_URL = "sqlite:///./bureau.db"


def database_url() -> str:
    return normalise(os.environ.get("DATABASE_URL") or DEFAULT_URL)


def normalise(url: str) -> str:
    """Point a bare `postgresql://` URL at the driver we actually ship.

    SQLAlchemy reads `postgresql://` as psycopg2, and we ship psycopg 3. Everyone writes
    the bare form — it is what Postgres itself prints, what every hosting provider hands
    out, and what `docker compose` had — so accepting it is kinder than an import error
    that names a driver nobody chose.
    """
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


def _register_tables() -> None:
    """Importing the models is what puts them in `SQLModel.metadata`, so anything that
    creates tables has to have done it first."""
    from bureau_api import models  # noqa: F401


def build_engine(url: str | None = None) -> object:
    _register_tables()
    url = url or database_url()
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=connect_args, pool_pre_ping=True)


_engine = None


def engine() -> object:
    global _engine
    if _engine is None:
        _engine = build_engine()
        SQLModel.metadata.create_all(_engine)  # type: ignore[arg-type]
    return _engine


def reset(url: str) -> None:
    """Used by the tests and by `bureau-api init`; never in a request."""
    global _engine
    _engine = build_engine(url)
    SQLModel.metadata.create_all(_engine)  # type: ignore[arg-type]


@contextmanager
def session() -> Iterator[Session]:
    with Session(engine()) as active:  # type: ignore[arg-type]
        yield active


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    with session() as active:
        yield active


def artifact_root() -> Path:
    root = Path(os.environ.get("ARTIFACTS_DIR") or "runs")
    root.mkdir(parents=True, exist_ok=True)
    return root
