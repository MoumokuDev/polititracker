"""Committee assignments — jurisdiction context for votes, trades, and donors.

Source of truth is unitedstates/congress-legislators (committees-current +
committee-membership-current). Phase 1 tracks main committees only.
"""

from sqlalchemy import (
    BigInteger,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from polititracker.models.base import Base, TimestampMixin


class Committee(Base, TimestampMixin):
    __tablename__ = "committee"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    thomas_id: Mapped[str] = mapped_column(String(8), unique=True)
    name: Mapped[str] = mapped_column(String(256))
    chamber: Mapped[str] = mapped_column(String(8))  # house | senate | joint
    url: Mapped[str | None] = mapped_column(Text)
    source_fetch_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("source_fetch.id"))

    memberships: Mapped[list["CommitteeMembership"]] = relationship(back_populates="committee")


class CommitteeMembership(Base, TimestampMixin):
    __tablename__ = "committee_membership"
    __table_args__ = (
        UniqueConstraint("committee_id", "figure_id", name="uq_committee_membership_identity"),
        Index("ix_committee_membership_figure_id", "figure_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    committee_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("committee.id"))
    figure_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("figure.id"))
    role_title: Mapped[str | None] = mapped_column(String(64))  # Chairman, Ranking Member, ...
    rank: Mapped[int | None] = mapped_column(SmallInteger)
    party_side: Mapped[str | None] = mapped_column(String(16))  # majority | minority
    source_fetch_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("source_fetch.id"))

    committee: Mapped[Committee] = relationship(back_populates="memberships")
