"""SQLAlchemy sessions use the same PostgreSQL connection pool as readiness checks."""
from collections.abc import Generator
from functools import lru_cache

from sqlalchemy.orm import Session, sessionmaker

from .database import engine


@lru_cache
def session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=engine(), expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    with session_factory()() as db:
        yield db
