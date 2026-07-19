"""Precomputed per-figure statistics.

Each row is arithmetic over records already in this database, never opinion:
the method field names the exact computation, and numerator/denominator are
stored so every displayed percentage can be checked by hand.
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from polititracker.models.base import Base


class FigureStat(Base):
    __tablename__ = "figure_stat"
    __table_args__ = (
        UniqueConstraint("figure_id", "key", name="uq_figure_stat_identity"),
        Index("ix_figure_stat_figure_id", "figure_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    figure_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("figure.id"))
    key: Mapped[str] = mapped_column(String(48))  # e.g. party_unity
    value: Mapped[float] = mapped_column(Float)  # usually a percentage
    numerator: Mapped[int | None] = mapped_column(Integer)
    denominator: Mapped[int | None] = mapped_column(Integer)
    method: Mapped[str] = mapped_column(String(64))
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
