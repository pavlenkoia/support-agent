from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings


class Base(DeclarativeBase):
    pass



def make_engine(database_url: str):
    kwargs: dict = {"future": True}

    if database_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if ":memory:" in database_url or database_url.endswith("://"):
            kwargs["poolclass"] = StaticPool

    return create_engine(database_url, **kwargs)



def make_session_factory(database_url: str):
    engine = make_engine(database_url)
    return sessionmaker(bind=engine, future=True, expire_on_commit=False)


engine = make_engine(settings.database_url)
SessionLocal = make_session_factory(settings.database_url)

# Import model modules so Base.metadata is populated for create_all/tests.
import app.models  # noqa: E402,F401
