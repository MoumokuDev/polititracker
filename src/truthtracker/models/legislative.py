from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from truthtracker.models.base import Base, TimestampMixin
from truthtracker.models.entity import chamber_enum

vote_position_enum = Enum("yea", "nay", "present", "not_voting", "other", name="vote_position")


class Bill(Base, TimestampMixin):
    __tablename__ = "bill"
    __table_args__ = (
        UniqueConstraint("congress", "bill_type", "number", name="uq_bill_identity"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    congress: Mapped[int] = mapped_column(SmallInteger)
    bill_type: Mapped[str] = mapped_column(String(8))  # hr, s, hjres, sjres, ...
    number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str | None] = mapped_column(Text)
    policy_area: Mapped[str | None] = mapped_column(String(128))  # Congress.gov taxonomy
    latest_action_date: Mapped[date | None] = mapped_column(Date)
    latest_action_text: Mapped[str | None] = mapped_column(Text)
    # Official CRS summary (Congressional Research Service, via Congress.gov) —
    # government-authored; this project never generates summaries itself.
    summary: Mapped[str | None] = mapped_column(Text)
    summary_date: Mapped[date | None] = mapped_column(Date)
    summary_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_url: Mapped[str] = mapped_column(Text)
    source_fetch_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("source_fetch.id"))


class BillSponsorship(Base, TimestampMixin):
    __tablename__ = "bill_sponsorship"
    __table_args__ = (
        UniqueConstraint("bill_id", "figure_id", name="uq_bill_sponsorship_identity"),
        Index("ix_bill_sponsorship_figure_id", "figure_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    bill_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("bill.id"))
    figure_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("figure.id"))
    is_original: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    sponsored_date: Mapped[date | None] = mapped_column(Date)
    source_fetch_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("source_fetch.id"))


class RollCall(Base, TimestampMixin):
    __tablename__ = "roll_call"
    __table_args__ = (
        UniqueConstraint(
            "congress", "chamber", "session", "roll_number", name="uq_roll_call_identity"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    congress: Mapped[int] = mapped_column(SmallInteger)
    chamber: Mapped[str] = mapped_column(chamber_enum)
    session: Mapped[int] = mapped_column(SmallInteger)
    roll_number: Mapped[int] = mapped_column(Integer)
    question: Mapped[str | None] = mapped_column(Text)
    result: Mapped[str | None] = mapped_column(String(128))
    vote_date: Mapped[date] = mapped_column(Date, index=True)
    bill_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("bill.id"))
    source_url: Mapped[str] = mapped_column(Text)
    source_fetch_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("source_fetch.id"))

    votes: Mapped[list["VoteCast"]] = relationship(back_populates="roll_call")


class VoteCast(Base):
    __tablename__ = "vote_cast"
    __table_args__ = (Index("ix_vote_cast_figure_id", "figure_id"),)

    roll_call_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("roll_call.id"), primary_key=True
    )
    figure_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("figure.id"), primary_key=True)
    position: Mapped[str] = mapped_column(vote_position_enum)
    # Verbatim position string from the source ("Present, Giving Live Pair").
    # The normalized enum is for queries; this column is the record.
    position_raw: Mapped[str] = mapped_column(String(64))

    roll_call: Mapped[RollCall] = relationship(back_populates="votes")
