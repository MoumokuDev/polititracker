"""Accountability records: documented public events only, append-only.

Each row is a dated EVENT with a required primary/authoritative source —
an indictment, a dismissal, a pardon, an ethics committee action, an OCE
referral, an FEC enforcement matter. A case's evolution is a sequence of
entries, never a mutable verdict field. The tool asserts nothing beyond what
each linked record says, and the UI attaches presumption-of-innocence language
to accusatory record types automatically.

Reputation, rumor, and unpublished allegations are not records and have no
schema here by design.
"""

from datetime import date

from sqlalchemy import BigInteger, Date, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from polititracker.models.base import Base, TimestampMixin

RECORD_TYPE_LABELS = {
    "indictment": "Indictment",
    "charge_dismissal": "Charges dismissed",
    "conviction": "Conviction",
    "acquittal": "Acquittal",
    "pardon": "Pardon",
    "plea": "Plea",
    "ethics_committee_action": "Ethics Committee action",
    "oce_referral": "Office of Congressional Ethics referral",
    "censure_vote": "Censure vote",
    "reprimand_vote": "Reprimand vote",
    "expulsion_vote": "Expulsion vote",
    "fec_enforcement": "FEC enforcement matter",
    "civil_judgment": "Civil judgment",
    "settlement": "Settlement",
    "other": "Other documented record",
}

# record types that are accusations, not findings — the UI must say so
ACCUSATORY_TYPES = {"indictment", "oce_referral", "fec_enforcement"}


class AccountabilityRecord(Base, TimestampMixin):
    __tablename__ = "accountability_record"
    __table_args__ = (Index("ix_accountability_record_figure_id", "figure_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    figure_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("figure.id"))
    record_type: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(250))  # factual headline
    description: Mapped[str] = mapped_column(Text)  # factual summary of the record
    occurred_on: Mapped[date] = mapped_column(Date)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    docket_number: Mapped[str | None] = mapped_column(String(100))
    status_note: Mapped[str | None] = mapped_column(String(300))
