"""Committee-level FEC summaries only.

Legal constraint (52 U.S.C. 30111(a)(4)): individual contributor data must not
be used for solicitation or commercial purposes. Phase 1 deliberately stores no
itemized receipts; re-review before ever adding them.
"""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from truthtracker.models.base import Base, TimestampMixin


class FinanceSummary(Base, TimestampMixin):
    __tablename__ = "finance_summary"
    __table_args__ = (
        UniqueConstraint(
            "figure_id", "fec_candidate_id", "cycle", name="uq_finance_summary_identity"
        ),
        Index("ix_finance_summary_figure_id", "figure_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    figure_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("figure.id"))
    fec_candidate_id: Mapped[str] = mapped_column(String(16))
    cycle: Mapped[int] = mapped_column(SmallInteger)
    total_receipts: Mapped[float | None] = mapped_column(Numeric(14, 2))
    total_disbursements: Mapped[float | None] = mapped_column(Numeric(14, 2))
    cash_on_hand: Mapped[float | None] = mapped_column(Numeric(14, 2))
    debts: Mapped[float | None] = mapped_column(Numeric(14, 2))
    # receipt composition (all FEC aggregates — never itemized individuals)
    individual_itemized: Mapped[float | None] = mapped_column(Numeric(14, 2))
    individual_unitemized: Mapped[float | None] = mapped_column(Numeric(14, 2))
    pac_contributions: Mapped[float | None] = mapped_column(Numeric(14, 2))
    party_contributions: Mapped[float | None] = mapped_column(Numeric(14, 2))
    candidate_self: Mapped[float | None] = mapped_column(Numeric(14, 2))
    coverage_end_date: Mapped[date | None] = mapped_column(Date)
    source_url: Mapped[str] = mapped_column(Text)
    source_fetch_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("source_fetch.id"))
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FinanceSource(Base, TimestampMixin):
    """Top contribution sources per committee-cycle, as aggregated BY THE FEC.

    Currently source_type == 'employer': Schedule A receipts grouped by the
    contributor-reported employer field (fec.gov /schedules/schedule_a/by_employer/).
    Aggregates only — no individual contributor names are ever stored. Employer
    strings appear exactly as filed ("RETIRED", "SELF-EMPLOYED", "NULL", ...).
    """

    __tablename__ = "finance_source"
    __table_args__ = (
        UniqueConstraint(
            "figure_id",
            "fec_committee_id",
            "cycle",
            "source_type",
            "name",
            name="uq_finance_source_identity",
        ),
        Index("ix_finance_source_figure_id", "figure_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    figure_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("figure.id"))
    fec_committee_id: Mapped[str] = mapped_column(String(16))
    cycle: Mapped[int] = mapped_column(SmallInteger)
    source_type: Mapped[str] = mapped_column(String(16), default="employer")
    name: Mapped[str] = mapped_column(String(256))
    total: Mapped[float | None] = mapped_column(Numeric(14, 2))
    contribution_count: Mapped[int | None] = mapped_column(Integer)
    source_url: Mapped[str] = mapped_column(Text)
    source_fetch_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("source_fetch.id"))
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
