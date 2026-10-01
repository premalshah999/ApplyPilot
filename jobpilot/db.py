from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import JSON, Boolean, Float, Integer, String, Text, create_engine, event, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def now():
    return datetime.now(UTC).isoformat()


def uid():
    return uuid4().hex


class Base(DeclarativeBase):
    pass


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)


class Resume(Base):
    __tablename__ = "resumes"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String)
    filename: Mapped[str] = mapped_column(String)
    sha256: Mapped[str] = mapped_column(String, unique=True)
    roles: Mapped[list] = mapped_column(JSON, default=list)
    text: Mapped[str] = mapped_column(Text, default="")
    demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[str] = mapped_column(String, default=now)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    identity: Mapped[str] = mapped_column(String, unique=True)
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(String, default="")
    company: Mapped[str] = mapped_column(String, default="")
    location: Mapped[str] = mapped_column(String, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    ats: Mapped[str] = mapped_column(String, default="custom")
    status: Mapped[str] = mapped_column(String, default="new")
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    resume_id: Mapped[str | None] = mapped_column(String, nullable=True)
    demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[str] = mapped_column(String, default=now)


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    job_id: Mapped[str] = mapped_column(String, index=True)
    state: Mapped[str] = mapped_column(String, default="queued", index=True)
    mode: Mapped[str] = mapped_column(String, default="dry_run")
    resume_id: Mapped[str | None] = mapped_column(String, nullable=True)
    packet: Mapped[dict] = mapped_column(JSON, default=dict)
    started_at: Mapped[str | None] = mapped_column(String, nullable=True)
    finished_at: Mapped[str | None] = mapped_column(String, nullable=True)
    elapsed: Mapped[float] = mapped_column(Float, default=0)
    cost: Mapped[float] = mapped_column(Float, default=0)
    model_calls: Mapped[int] = mapped_column(Integer, default=0)
    reason: Mapped[str] = mapped_column(Text, default="")
    receipt: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[str] = mapped_column(String, default=now)


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String, index=True)
    kind: Mapped[str] = mapped_column(String)
    message: Mapped[str] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[str] = mapped_column(String, default=now)


class Review(Base):
    __tablename__ = "reviews"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    run_id: Mapped[str] = mapped_column(String, index=True)
    question: Mapped[str] = mapped_column(Text)
    options: Mapped[list] = mapped_column(JSON, default=list)
    key: Mapped[str] = mapped_column(String)
    reason: Mapped[str] = mapped_column(Text, default="")
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String, default=now)


class Source(Base):
    __tablename__ = "sources"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String)
    url: Mapped[str] = mapped_column(Text)
    cron: Mapped[str] = mapped_column(String)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_queue: Mapped[bool] = mapped_column(Boolean, default=False)
    next_run: Mapped[str] = mapped_column(String, default=now)
    last_run: Mapped[str | None] = mapped_column(String, nullable=True)
    error: Mapped[str] = mapped_column(Text, default="")
    found: Mapped[int] = mapped_column(Integer, default=0)


class Budget(Base):
    __tablename__ = "budgets"
    day: Mapped[str] = mapped_column(String, primary_key=True)
    spent: Mapped[float] = mapped_column(Float, default=0)
    submissions: Mapped[int] = mapped_column(Integer, default=0)


def record(obj):
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}


class Database:
    def __init__(self, url):
        self.engine = create_engine(
            url,
            connect_args={"check_same_thread": False, "timeout": 30} if url.startswith("sqlite") else {},
            pool_pre_ping=True,
        )
        if url.startswith("sqlite"):

            @event.listens_for(self.engine, "connect")
            def setup(connection, _):
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA busy_timeout=30000")

        self.Session = sessionmaker(self.engine, expire_on_commit=False)
        Base.metadata.create_all(self.engine)
        with self.session() as s:
            if not s.get(Setting, "control"):
                s.add(Setting(key="control", value={"paused": False, "auto_submit": False}))

    @contextmanager
    def session(self):
        with self.Session.begin() as session:
            yield session

    @contextmanager
    def exclusive(self):
        """Short business transactions; never hold this while doing network work."""
        with self.Session() as s:
            if self.engine.dialect.name == "sqlite":
                s.connection().exec_driver_sql("BEGIN IMMEDIATE")
            else:
                # One per-app lock for budget/cap reservations and job transitions.
                s.connection().exec_driver_sql("SELECT pg_advisory_xact_lock(72319001)")
            try:
                yield s
                s.commit()
            except BaseException:
                s.rollback()
                raise

    def event(self, run_id, kind, message, data=None):
        with self.session() as s:
            s.add(Event(run_id=run_id, kind=kind, message=message, data=data or {}))

    def get_setting(self, key, default=None):
        with self.session() as s:
            row = s.get(Setting, key)
            return row.value if row else default

    def set_setting(self, key, value):
        with self.exclusive() as s:
            row = s.get(Setting, key)
            if row:
                row.value = value
            else:
                s.add(Setting(key=key, value=value))

    def list(self, model, limit=500):
        with self.session() as s:
            return [record(x) for x in s.scalars(select(model).limit(limit))]
