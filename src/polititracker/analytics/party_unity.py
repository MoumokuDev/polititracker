"""Party-unity scores, CQ-style, computed from stored full-chamber vote payloads.

Methodology (stated on the profile wherever the number appears):
- Only "party-unity votes" count: roll calls where a majority of voting
  Democrats and a majority of voting Republicans took opposite positions.
- Only Yea/Nay positions count; absences and Present are excluded from both
  sides of the ratio.
- score = votes cast with one's own party's majority / party-unity votes cast.

Every roll call's full member breakdown already lives in source_fetch (House:
the Congress.gov members payload with voteParty; Senate: the LIS XML with per-
member party), so this is pure arithmetic over records — no new API calls.
"""

import logging
import xml.etree.ElementTree as ET
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from polititracker.ingestion.base import ingestion_run
from polititracker.ingestion.votes_common import normalize_position
from polititracker.models import Figure, FigureStat, RollCall, SourceFetch, VoteCast

log = logging.getLogger(__name__)

ADAPTER = "party_unity"
KEY = "party_unity"
METHOD = "cq_divided_votes_v1"

_PARTY_CODES = {"Republican": "R", "Democrat": "D", "Democratic": "D"}


def _tally_house(payload: dict) -> dict[str, dict[str, int]]:
    tally: dict[str, dict[str, int]] = {}
    for member in payload.get("results", []):
        party = member.get("voteParty")
        position = normalize_position(member.get("voteCast", ""))
        if party in ("R", "D") and position in ("yea", "nay"):
            tally.setdefault(party, {"yea": 0, "nay": 0})[position] += 1
    return tally


def _tally_senate(payload: dict) -> dict[str, dict[str, int]]:
    tally: dict[str, dict[str, int]] = {}
    root = ET.fromstring(payload.get("xml", "<x/>"))
    for member in root.iter("member"):
        party = (member.findtext("party") or "").strip()
        position = normalize_position(member.findtext("vote_cast") or "")
        if party in ("R", "D") and position in ("yea", "nay"):
            tally.setdefault(party, {"yea": 0, "nay": 0})[position] += 1
    return tally


def _majority(counts: dict[str, int] | None) -> str | None:
    if not counts:
        return None
    if counts["yea"] > counts["nay"]:
        return "yea"
    if counts["nay"] > counts["yea"]:
        return "nay"
    return None  # tie: no majority


def run(session: Session) -> dict:
    with ingestion_run(session, ADAPTER) as run_row:
        # 1. per roll call: each party's majority position, from the raw payload
        rolls = session.execute(
            select(RollCall.id, RollCall.chamber, SourceFetch.payload)
            .join(SourceFetch, SourceFetch.id == RollCall.source_fetch_id)
        ).all()
        run_row.records_seen = len(rolls)

        majorities: dict[int, dict[str, str]] = {}  # roll_call_id -> {party: position}
        divided: set[int] = set()
        skipped = 0
        for roll_id, chamber, payload in rolls:
            try:
                tally = _tally_house(payload) if chamber == "house" else _tally_senate(payload)
            except Exception:
                skipped += 1
                continue
            r_major, d_major = _majority(tally.get("R")), _majority(tally.get("D"))
            if r_major is None or d_major is None:
                continue
            majorities[roll_id] = {"R": r_major, "D": d_major}
            if r_major != d_major:
                divided.add(roll_id)

        # 2. per tracked figure: agreement with own party on divided votes
        figures = (
            session.scalars(
                select(Figure)
                .options(selectinload(Figure.roles))
                .where(Figure.branch == "legislative", Figure.is_active)
            )
        ).all()
        computed = 0
        now = datetime.now(UTC)
        for figure in figures:
            role = figure.roles[-1] if figure.roles else None
            party = _PARTY_CODES.get(role.party if role else "")
            if party is None:
                continue
            casts = session.execute(
                select(VoteCast.roll_call_id, VoteCast.position).where(
                    VoteCast.figure_id == figure.id
                )
            ).all()
            agree = total = 0
            for roll_id, position in casts:
                if roll_id not in divided or position not in ("yea", "nay"):
                    continue
                total += 1
                if position == majorities[roll_id][party]:
                    agree += 1
            if total == 0:
                continue
            stat = session.scalar(
                select(FigureStat).where(
                    FigureStat.figure_id == figure.id, FigureStat.key == KEY
                )
            )
            if stat is None:
                stat = FigureStat(figure_id=figure.id, key=KEY, value=0.0, method=METHOD)
                session.add(stat)
            stat.value = round(100.0 * agree / total, 1)
            stat.numerator = agree
            stat.denominator = total
            stat.method = METHOD
            stat.computed_at = now
            computed += 1
        session.commit()

        run_row.records_upserted = computed
        summary = {
            "adapter": ADAPTER,
            "roll_calls": len(rolls),
            "divided_votes": len(divided),
            "unparseable_payloads": skipped,
            "figures_scored": computed,
        }
        log.info("%s", summary)
        return summary
