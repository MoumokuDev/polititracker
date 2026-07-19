"""Entity backbone: figures, their time-scoped roles, and external ID crosswalk.

Party/state/district/office are facts about a dated role, not about a person —
people switch parties, chambers, and jobs. figure_role mirrors the `terms`
structure of unitedstates/congress-legislators and generalizes to executive and
judicial appointments (is_acting covers acting cabinet officials).
"""

from datetime import date

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    Enum,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from polititracker.models.base import Base, TimestampMixin

branch_enum = Enum("legislative", "executive", "judicial", name="branch")
chamber_enum = Enum("house", "senate", name="chamber")


class Figure(Base, TimestampMixin):
    __tablename__ = "figure"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    slug: Mapped[str] = mapped_column(String(128), unique=True)
    full_name: Mapped[str] = mapped_column(String(256))
    first_name: Mapped[str | None] = mapped_column(String(128))
    last_name: Mapped[str | None] = mapped_column(String(128))
    branch: Mapped[str] = mapped_column(branch_enum, index=True)
    bioguide_id: Mapped[str | None] = mapped_column(String(16), unique=True)
    birthday: Mapped[date | None] = mapped_column(Date)
    official_url: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    source_fetch_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("source_fetch.id"))

    roles: Mapped[list["FigureRole"]] = relationship(
        back_populates="figure", order_by="FigureRole.start_date"
    )
    external_ids: Mapped[list["ExternalId"]] = relationship(back_populates="figure")


class FigureRole(Base, TimestampMixin):
    __tablename__ = "figure_role"
    __table_args__ = (
        UniqueConstraint("figure_id", "role_type", "start_date", name="uq_figure_role_identity"),
        Index("ix_figure_role_figure_id", "figure_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    figure_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("figure.id"))
    # Open set: rep, sen, president, vice_president, cabinet_secretary, scotus_justice, ...
    role_type: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(256))
    chamber: Mapped[str | None] = mapped_column(chamber_enum)
    state: Mapped[str | None] = mapped_column(String(2))
    district: Mapped[int | None] = mapped_column(SmallInteger)  # 0 = at-large
    party: Mapped[str | None] = mapped_column(String(64))
    is_acting: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Nullable: an unverified date is stored as NULL, never approximated.
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    source_url: Mapped[str | None] = mapped_column(Text)
    source_fetch_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("source_fetch.id"))

    figure: Mapped[Figure] = relationship(back_populates="roles")


class ExternalId(Base, TimestampMixin):
    __tablename__ = "external_id"
    __table_args__ = (
        UniqueConstraint("id_type", "id_value", name="uq_external_id_identity"),
        Index("ix_external_id_figure_id", "figure_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    figure_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("figure.id"))
    # bioguide, fec, govtrack, opensecrets, votesmart, icpsr, lis, wikidata,
    # cspan, twitter, youtube, ... (a figure may hold several fec ids)
    id_type: Mapped[str] = mapped_column(String(32))
    id_value: Mapped[str] = mapped_column(String(256))
    source_fetch_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("source_fetch.id"))

    figure: Mapped[Figure] = relationship(back_populates="external_ids")
