"""SQLAlchemy database models — Alembic-compatible plain ORM."""

from __future__ import annotations

import uuid

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Session, relationship, sessionmaker

from agentbench.server.config import settings


class Base(DeclarativeBase):
    """Base class for all ORM models."""


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    username = Column(String(255), unique=True, nullable=False, index=True)
    email = Column(String(255), unique=True, nullable=True)
    api_key_hash = Column(String(512), nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    projects = relationship("Project", back_populates="owner", lazy="selectin")


class Project(Base):
    __tablename__ = "projects"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(255), nullable=False, index=True)
    description = Column(Text, nullable=True)
    principal = Column(String(255), nullable=False, index=True, default="")
    owner_id = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    owner = relationship("User", back_populates="projects")
    test_suites = relationship("TestSuite", back_populates="project", lazy="selectin")
    saved_agents = relationship("SavedAgent", back_populates="project", lazy="selectin")
    scan_policies = relationship("ScanPolicy", back_populates="project", lazy="selectin")


class SavedAgent(Base):
    __tablename__ = "saved_agents"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(String, ForeignKey("projects.id"), nullable=False, index=True)
    principal = Column(String(255), nullable=False, index=True, default="")
    name = Column(String(255), nullable=False)
    agent_url = Column(Text, nullable=False)
    created_at = Column(DateTime, server_default=func.now())

    project = relationship("Project", back_populates="saved_agents")


class ScanPolicy(Base):
    __tablename__ = "scan_policies"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(String, ForeignKey("projects.id"), nullable=False, index=True)
    principal = Column(String(255), nullable=False, index=True, default="")
    name = Column(String(255), nullable=False)
    categories_json = Column(Text, nullable=True)
    minimum_overall_score = Column(Float, nullable=True)
    minimum_domain_scores_json = Column(Text, nullable=False, default="{}", server_default="{}")
    fail_on_critical_issues = Column(Integer, nullable=False, default=1, server_default="1")
    max_regression_delta = Column(Float, nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    project = relationship("Project", back_populates="scan_policies")


class ScanJob(Base):
    __tablename__ = "scan_jobs"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    principal = Column(String(255), nullable=False, index=True, default="")
    status = Column(String(50), nullable=False, default="queued", server_default="queued")
    cancel_requested = Column(Integer, nullable=False, default=0, server_default="0")
    agent_url = Column(Text, nullable=False)
    project_id = Column(String, nullable=True, index=True)
    agent_id = Column(String, nullable=True, index=True)
    policy_id = Column(String, nullable=True, index=True)
    categories_json = Column(Text, nullable=True)
    scan_id = Column(String, nullable=True, index=True)
    release_verdict = Column(String(50), nullable=True)
    verdict_reasons_json = Column(Text, nullable=False, default="[]", server_default="[]")
    overall_score = Column(Float, nullable=True)
    overall_grade = Column(String(10), nullable=True)
    error_detail = Column(Text, nullable=True)
    report_json = Column(Text, nullable=True)
    domain_scores_json = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)


class TestSuite(Base):
    __tablename__ = "test_suites"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    code = Column(Text, nullable=True)  # Inline test suite code
    path = Column(String(1024), nullable=True)  # Path to test suite on disk
    project_id = Column(String, ForeignKey("projects.id"), nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    project = relationship("Project", back_populates="test_suites")
    runs = relationship("Run", back_populates="test_suite", lazy="selectin")


class Run(Base):
    __tablename__ = "runs"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    test_suite_id = Column(String, ForeignKey("test_suites.id"), nullable=True)
    principal = Column(String(255), nullable=False, index=True, default="")
    status = Column(
        String(50), default="pending", server_default="pending"
    )  # pending, running, completed, failed
    total_tests = Column(Integer, default=0, server_default="0")
    passed = Column(Integer, default=0, server_default="0")
    failed = Column(Integer, default=0, server_default="0")
    duration_ms = Column(Float, default=0.0, server_default="0.0")
    created_at = Column(DateTime, server_default=func.now())
    completed_at = Column(DateTime, nullable=True)

    test_suite = relationship("TestSuite", back_populates="runs")
    results = relationship("RunResult", back_populates="run", lazy="selectin")


class RunResult(Base):
    __tablename__ = "run_results"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    run_id = Column(String, ForeignKey("runs.id"), nullable=False)
    test_name = Column(String(255), nullable=False)
    passed = Column(Integer, default=0, server_default="0")
    failed = Column(Integer, default=0, server_default="0")
    duration_ms = Column(Float, default=0.0, server_default="0.0")
    error = Column(Text, nullable=True)
    assertions_json = Column(Text, nullable=True)  # JSON-serialized assertions

    run = relationship("Run", back_populates="results")


class Trajectory(Base):
    __tablename__ = "trajectories"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    principal = Column(String(255), nullable=False, index=True, default="")
    name = Column(String(255), nullable=False, index=True)
    data = Column(Text, nullable=False)  # JSON blob of full trajectory
    prompt = Column(Text, nullable=True)
    step_count = Column(Integer, default=0, server_default="0")
    tags = Column(String(512), nullable=True)  # comma-separated tags
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


# ---------------------------------------------------------------------------
# Engine / session helpers
# ---------------------------------------------------------------------------

from sqlalchemy import create_engine as _create_engine  # noqa: E402

_engine = None
_SessionLocal: sessionmaker | None = None


def get_engine():
    """Return the shared SQLAlchemy engine (lazy singleton)."""
    global _engine
    if _engine is None:
        _engine = _create_engine(
            settings.database_url,
            echo=settings.debug,
            pool_pre_ping=True,
            connect_args={"check_same_thread": False}
            if "sqlite" in settings.database_url
            else {},
        )
    return _engine


def get_session_factory() -> sessionmaker:
    """Return a session factory bound to the current engine."""
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=get_engine())
    return _SessionLocal


def get_db():
    """FastAPI dependency that yields a DB session and closes it after the request."""
    factory = get_session_factory()
    db: Session = factory()
    try:
        yield db
    finally:
        db.close()


def create_tables() -> None:
    """Create all tables — for dev setup only (use Alembic in production)."""
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
