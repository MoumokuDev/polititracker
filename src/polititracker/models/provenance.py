"""Provenance backbone.

Every domain row FKs into source_fetch, so any displayed fact can be traced to
the exact raw payload it was parsed from — and reprocessing never re-hits APIs.
source_package records which source units (e.g. CREC daily issues) the corpus
actually covers, so "no match" answers can state their bounds honestly.
"""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from polititracker.models.base import Base

run_status_enum = Enum("running", "success", "failure", name="run_status")


class SourceFetch(Base):
    __tablename__ = "source_fetch"
    __table_args__ = (
        UniqueConstraint("adapter", "native_id", "content_hash", name="uq_source_fetch_identity"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    adapter: Mapped[str] = mapped_column(String(64), index=True)
    native_id: Mapped[str] = mapped_column(String(512))
    source_url: Mapped[str] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    source_version: Mapped[str | None] = mapped_column(String(256))
    content_hash: Mapped[str] = mapped_column(String(64))  # sha256 hex of payload
    payload: Mapped[dict] = mapped_column(JSONB)


class SourcePackage(Base):
    """One row per ingested source unit (CREC daily issue, hearing, FR doc set).

    The set of these rows IS the corpus coverage map. A claim-search miss must
    report coverage from here, never a bare "no match".
    """

    __tablename__ = "source_package"
    __table_args__ = (
        UniqueConstraint("adapter", "package_id", name="uq_source_package_identity"),
        CheckConstraint(
            "status IN ('pending', 'ingested', 'failed')", name="status_allowed"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    adapter: Mapped[str] = mapped_column(String(64), index=True)
    package_id: Mapped[str] = mapped_column(String(512))
    issue_date: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    statement_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    source_fetch_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("source_fetch.id")
    )
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IngestionRun(Base):
    """Fail-loudly ledger of adapter runs; powers the health dashboard."""

    __tablename__ = "ingestion_run"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    adapter: Mapped[str] = mapped_column(String(64), index=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(run_status_enum, default="running")
    records_seen: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    records_upserted: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    error: Mapped[str | None] = mapped_column(Text)
