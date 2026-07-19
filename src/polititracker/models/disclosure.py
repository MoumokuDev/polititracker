"""Financial disclosure filings (STOCK Act) — the official filing index.

Rows mirror the House Clerk's public filing index: who filed what, when, with
a direct link to the official PDF on disclosures-clerk.house.gov. The tool
lists filings; it does not parse or interpret the transactions inside them.
"""

from datetime import date

from sqlalchemy import (
    BigInteger,
    Date,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from polititracker.models.base import Base, TimestampMixin

FILING_TYPE_LABELS = {
    "P": "Periodic transaction report (STOCK Act)",
    "O": "Annual financial disclosure",
    "A": "Amendment",
    "C": "Candidate report",
    "X": "Extension request",
    "T": "Termination report",
    "D": "Blind trust / other",
}


class DisclosureFiling(Base, TimestampMixin):
    __tablename__ = "disclosure_filing"
    __table_args__ = (
        UniqueConstraint("figure_id", "doc_id", name="uq_disclosure_filing_identity"),
        Index("ix_disclosure_filing_figure_id", "figure_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    figure_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("figure.id"))
    doc_id: Mapped[str] = mapped_column(String(32))
    filing_type_code: Mapped[str] = mapped_column(String(8))
    filing_year: Mapped[int] = mapped_column(SmallInteger)
    filing_date: Mapped[date | None] = mapped_column(Date)
    source_url: Mapped[str] = mapped_column(Text)  # official PDF on the Clerk's site
    source_fetch_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("source_fetch.id"))
