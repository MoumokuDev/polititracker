"""Statement corpus — source-agnostic per spec.

Pipeline invariant (enforced in ingestion tests, stated here for the record):
utterance_text and context_window MUST be verbatim substrings of the raw
source_fetch payload they FK to. Nothing in this table is ever generated.
"""

from datetime import date, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Computed,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from polititracker.models.base import Base

EMBEDDING_DIM = 384  # bge-small-en-v1.5


class Statement(Base):
    __tablename__ = "statement"
    __table_args__ = (
        UniqueConstraint("source_type", "native_id", name="uq_statement_identity"),
        Index("ix_statement_figure_id_occurred_on", "figure_id", "occurred_on"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    figure_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("figure.id"))
    utterance_text: Mapped[str] = mapped_column(Text)
    # Sources give day precision (CREC); occurred_at only when time-of-day is
    # in the source. Never fabricate precision.
    occurred_on: Mapped[date] = mapped_column(Date)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Open set: crec_floor, crec_extension, chrg_hearing, fedreg_doc,
    # wh_briefing, cl_opinion, oyez_argument, video_whisper, ...
    # crec_floor vs crec_extension matters: members insert remarks that were
    # never spoken; the UI must label them "submitted to the Record".
    source_type: Mapped[str] = mapped_column(String(32))
    source_url: Mapped[str] = mapped_column(Text, nullable=False)  # no claim without a URL
    context_window: Mapped[str | None] = mapped_column(Text)
    # Speaker-attribution confidence (1.0 = structured speaker header;
    # lower for heuristic parses or future Whisper diarization).
    confidence: Mapped[float] = mapped_column(Float, default=1.0, server_default="1.0")
    attribution_method: Mapped[str | None] = mapped_column(String(64))
    heading: Mapped[str | None] = mapped_column(Text)
    native_id: Mapped[str] = mapped_column(String(512))
    source_fetch_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("source_fetch.id"))
    source_package_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("source_package.id")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    chunks: Mapped[list["StatementChunk"]] = relationship(back_populates="statement")


class StatementChunk(Base):
    """Retrieval unit: embedding (semantic) + tsvector/trigram (lexical)."""

    __tablename__ = "statement_chunk"
    __table_args__ = (
        UniqueConstraint("statement_id", "chunk_index", name="uq_statement_chunk_identity"),
        Index(
            "ix_statement_chunk_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("ix_statement_chunk_tsv", "tsv", postgresql_using="gin"),
        Index(
            "ix_statement_chunk_trgm",
            "chunk_text",
            postgresql_using="gin",
            postgresql_ops={"chunk_text": "gin_trgm_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    statement_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("statement.id"))
    chunk_index: Mapped[int] = mapped_column(Integer)
    chunk_text: Mapped[str] = mapped_column(Text)
    embedding = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(128))
    tsv = mapped_column(
        TSVECTOR, Computed("to_tsvector('english', chunk_text)", persisted=True)
    )

    statement: Mapped[Statement] = relationship(back_populates="chunks")
