"""Explicit transaction ownership; PostgreSQL is the production backend."""

from contextlib import contextmanager

from sqlalchemy import create_engine, insert, inspect, select, text, update
from sqlalchemy.orm import sessionmaker

from .models import Base, Configuration, Session, Version

MAIN_SESSION = "00000000-0000-0000-0000-000000000001"


class Database:
    def __init__(self, settings):
        self.settings = settings
        if not settings.testing and not settings.database_url.startswith("postgresql"):
            raise ValueError("Production requires PostgreSQL")
        self.engine = create_engine(settings.database_url, pool_pre_ping=True)
        self.factory = sessionmaker(self.engine, expire_on_commit=False)

    @contextmanager
    def transaction(self):
        with self.factory.begin() as session:
            yield session

    def initialize(self):
        with self.engine.begin() as conn:
            if conn.dialect.name == "postgresql":
                conn.execute(text("SELECT pg_advisory_xact_lock(70423901)"))
            Base.metadata.create_all(conn)
            version = conn.scalar(select(Version.id))
            if version not in (None, 1, 2, 3, 4):
                raise RuntimeError("Unsupported database schema version")
            if "spec" not in {c["name"] for c in inspect(conn).get_columns("skills")}:
                conn.execute(
                    text("ALTER TABLE skills ADD COLUMN spec TEXT NOT NULL DEFAULT ''")
                )
            columns = {c["name"] for c in inspect(conn).get_columns("skills")}
            if "enabled" not in columns:
                conn.execute(
                    text(
                        "ALTER TABLE skills ADD COLUMN enabled BOOLEAN NOT NULL DEFAULT true"
                    )
                )
            if "deleted_at" not in columns:
                conn.execute(text("ALTER TABLE skills ADD COLUMN deleted_at FLOAT"))
            session_columns = {c["name"] for c in inspect(conn).get_columns("sessions")}
            if "memories" not in session_columns:
                conn.execute(text("ALTER TABLE sessions ADD COLUMN memories JSON"))
            if "memory_epoch" not in session_columns:
                conn.execute(
                    text(
                        "ALTER TABLE sessions ADD COLUMN memory_epoch INTEGER NOT NULL DEFAULT 0"
                    )
                )
            if version is None:
                conn.execute(insert(Version).values(id=4))
            elif version != 4:
                conn.execute(update(Version).where(Version.id == version).values(id=4))
        with self.transaction() as db:
            if not db.get(Session, MAIN_SESSION):
                db.add(Session(id=MAIN_SESSION, title="Workbench", kind="main"))
            if not db.get(Configuration, "model"):
                db.add(Configuration(key="model", value=self.settings.model_defaults()))

    def model_config(self):
        with self.transaction() as db:
            return dict(db.get(Configuration, "model").value)


def row_dict(row):
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}
