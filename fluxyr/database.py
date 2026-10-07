"""Explicit transaction ownership for SQLite and PostgreSQL."""

import threading
from contextlib import contextmanager, nullcontext

from sqlalchemy import create_engine, event, insert, inspect, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from .models import Base, Session, Version

MAIN_SESSION = "00000000-0000-0000-0000-000000000001"


class Database:
    def __init__(self, settings):
        self.settings = settings
        url = make_url(settings.database_url)
        self.sqlite = url.get_backend_name() == "sqlite"
        if url.get_backend_name() not in ("postgresql", "sqlite"):
            raise ValueError("DATABASE_URL must use SQLite or PostgreSQL")
        self._transaction_lock = threading.RLock()
        self._local = threading.local()
        options = {"pool_pre_ping": True}
        if self.sqlite:
            options["connect_args"] = {"check_same_thread": False, "timeout": 30}
            if url.database in (None, "", ":memory:"):
                options["poolclass"] = StaticPool
        self.engine = create_engine(url, **options)
        if self.sqlite:

            @event.listens_for(self.engine, "connect")
            def configure_sqlite(connection, _):
                connection.isolation_level = None
                cursor = connection.cursor()
                cursor.execute("PRAGMA busy_timeout=30000")
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.close()

            @event.listens_for(self.engine, "begin")
            def begin_sqlite(connection):
                # Claim/read-modify-write transactions must be atomic even though
                # SQLite does not implement SELECT FOR UPDATE / SKIP LOCKED.
                connection.exec_driver_sql("BEGIN IMMEDIATE")

        self.factory = sessionmaker(self.engine, expire_on_commit=False)

    @contextmanager
    def transaction(self):
        with self._transaction_lock if self.sqlite else nullcontext():
            parent = getattr(self._local, "session", None) if self.sqlite else None
            if parent is not None:
                with parent.begin_nested():
                    yield parent
                return
            with self.factory.begin() as session:
                if self.sqlite:
                    self._local.session = session
                try:
                    yield session
                finally:
                    if self.sqlite:
                        self._local.session = None

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

    def model_config(self):
        return self.settings.model_defaults()


def row_dict(row):
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}
