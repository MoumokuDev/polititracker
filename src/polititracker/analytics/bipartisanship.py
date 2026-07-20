"""Bipartisan cosponsorship rate.

Of the bills a member chose to cosponsor whose lead sponsor's party is known
and is one of the two major parties, the share led by a sponsor from the
OTHER major party. Sponsor data arrives via bill enrichment, so the
denominator grows as enrichment progresses; numerator and denominator are
stored and displayed so partial coverage is always visible.
"""

import logging
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from polititracker.ingestion.base import ingestion_run
from polititracker.models import Bill, BillSponsorship, Figure, FigureStat

log = logging.getLogger(__name__)

ADAPTER = "bipartisanship"
KEY = "bipartisan_cosponsorship"
METHOD = "cross_party_lead_sponsor_v1"

_PARTY_CODES = {"Republican": "R", "Democrat": "D", "Democratic": "D"}


def run(session: Session) -> dict:
    with ingestion_run(session, ADAPTER) as run_row:
        figures = (
            session.scalars(
                select(Figure)
                .options(selectinload(Figure.roles))
                .where(Figure.branch == "legislative", Figure.is_active)
            )
        ).all()
        run_row.records_seen = len(figures)

        computed = 0
        now = datetime.now(UTC)
        for figure in figures:
            role = figure.roles[-1] if figure.roles else None
            party = _PARTY_CODES.get(role.party if role else "")
            if party is None:
                continue
            rows = session.execute(
                select(Bill.sponsor_party, func.count())
                .join(BillSponsorship, BillSponsorship.bill_id == Bill.id)
                .where(
                    BillSponsorship.figure_id == figure.id,
                    BillSponsorship.is_original.is_(False),
                    Bill.sponsor_party.in_(("R", "D")),
                )
                .group_by(Bill.sponsor_party)
            ).all()
            counts = dict(rows)
            total = sum(counts.values())
            if total == 0:
                continue
            cross = total - counts.get(party, 0)
            stat = session.scalar(
                select(FigureStat).where(
                    FigureStat.figure_id == figure.id, FigureStat.key == KEY
                )
            )
            if stat is None:
                stat = FigureStat(figure_id=figure.id, key=KEY, value=0.0, method=METHOD)
                session.add(stat)
            stat.value = round(100.0 * cross / total, 1)
            stat.numerator = cross
            stat.denominator = total
            stat.method = METHOD
            stat.computed_at = now
            computed += 1
        session.commit()

        run_row.records_upserted = computed
        summary = {"adapter": ADAPTER, "figures_scored": computed}
        log.info("%s", summary)
        return summary
