"""Campaign promises & commitments — records first, judgment clearly labeled.

A promise is a verbatim quote with a primary source. Evidence rows are cited
records accumulated over time. `status` is an EDITORIAL ASSESSMENT made by a
named human editor with a written rationale — the application never computes
fulfillment, and the UI must always present the status as the editor's
judgment, visually distinct from the records themselves.
"""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
)
from sqlalchemy import (
    Date as SaDate,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from truthtracker.models.base import Base, TimestampMixin

promise_status_enum = Enum(
    "unassessed", "in_progress", "kept", "partially_kept", "not_kept", name="promise_status"
)

PROMISE_STATUS_LABELS = {
    "unassessed": "Not yet assessed",
    "in_progress": "In progress (editorial assessment)",
    "kept": "Kept (editorial assessment)",
    "partially_kept": "Partially kept (editorial assessment)",
    "not_kept": "Not kept (editorial assessment)",
}


class Promise(Base, TimestampMixin):
    __tablename__ = "promise"
    __table_args__ = (Index("ix_promise_figure_id", "figure_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    figure_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("figure.id"))
    # short handle for lists — an editor's paraphrase, labeled as such in the UI
    title: Mapped[str] = mapped_column(String(200))
    # the verbatim words; if statement_id is set this MUST be a substring of
    # that statement's utterance_text (enforced at creation)
    quote: Mapped[str] = mapped_column(Text)
    made_on: Mapped[date | None] = mapped_column(SaDate)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    statement_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("statement.id"))

    status: Mapped[str] = mapped_column(
        promise_status_enum, default="unassessed", server_default="unassessed"
    )
    assessment: Mapped[str | None] = mapped_column(Text)  # required when status != unassessed
    assessed_by: Mapped[str | None] = mapped_column(String(120))
    assessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    evidence: Mapped[list["PromiseEvidence"]] = relationship(
        back_populates="promise", order_by="PromiseEvidence.created_at"
    )


class PromiseEvidence(Base, TimestampMixin):
    __tablename__ = "promise_evidence"
    __table_args__ = (Index("ix_promise_evidence_promise_id", "promise_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    promise_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("promise.id"))
    kind: Mapped[str] = mapped_column(String(16))  # statement | vote | bill | external
    note: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    # optional structured links into the record (UI deep-linking is future work)
    statement_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("statement.id"))
    roll_call_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("roll_call.id"))
    bill_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("bill.id"))

    promise: Mapped[Promise] = relationship(back_populates="evidence")
