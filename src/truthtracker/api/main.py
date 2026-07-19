import asyncio
import sys
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from truthtracker import __version__
from truthtracker.config import get_settings
from truthtracker.db import get_async_session
from truthtracker.ingestion.adapters.portraits import available_portraits
from truthtracker.models import (
    ACCUSATORY_TYPES,
    FILING_TYPE_LABELS,
    PROMISE_STATUS_LABELS,
    RECORD_TYPE_LABELS,
    AccountabilityRecord,
    Bill,
    DisclosureFiling,
    Figure,
    FinanceSource,
    FinanceSummary,
    IngestionRun,
    Promise,
    RollCall,
    Statement,
    StatementTopic,
    Topic,
    VoteCast,
)
from truthtracker.search.embedder import embed_query
from truthtracker.search.retrieval import coverage_summary, hybrid_search

# psycopg async cannot run on Windows' default ProactorEventLoop (dev only;
# the Linux container is unaffected).
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

TX_HOUSE_SEATS = 38  # 2020 apportionment; revisit after the 2030 census

SOURCE_TYPE_LABELS = {
    "crec_floor": "Floor remarks — Congressional Record",
    "crec_extension": (
        "Submitted to the Record (Extensions of Remarks) — not delivered on the floor"
    ),
    "scotus_opinion": "Supreme Court opinion (authored)",
    "fedreg_presdoc": "Signed presidential document — Federal Register",
}

TIMELINE_STATEMENT_BADGES = {
    "crec_floor": "Spoke",
    "crec_extension": "Submitted remarks",
    "scotus_opinion": "Wrote opinion",
    "fedreg_presdoc": "Signed",
}


async def _run_search(
    session: AsyncSession,
    q: str,
    figure: str | None,
    date_from: date | None,
    date_to: date | None,
    limit: int,
) -> dict:
    query_vector = await run_in_threadpool(embed_query, q)
    matches = await hybrid_search(
        session,
        q,
        query_vector,
        figure_slug=figure,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
    )
    coverage = await coverage_summary(
        session, figure_slug=figure, date_from=date_from, date_to=date_to
    )
    threshold = get_settings().search_match_threshold
    best = max((m.similarity or 0.0 for m in matches), default=0.0)
    return {
        "query": q,
        "matches": matches,
        "coverage": coverage,
        "threshold": threshold,
        "threshold_met": best >= threshold,
    }

app = FastAPI(
    title="TruthTracker",
    version=__version__,
    description=(
        "Verifiable-provenance accountability data for US federal officials. "
        "Every fact links to its primary source."
    ),
)

_STATIC_DIR = Path(__file__).parent / "static"
_STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

from truthtracker.api.accountability import router as accountability_router  # noqa: E402
from truthtracker.api.auth import is_editor  # noqa: E402
from truthtracker.api.auth import router as auth_router  # noqa: E402
from truthtracker.api.promises import router as promises_router  # noqa: E402

app.include_router(auth_router)
app.include_router(promises_router)
app.include_router(accountability_router)


class RoleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    role_type: str
    title: str
    chamber: str | None
    state: str | None
    district: int | None
    party: str | None
    is_acting: bool
    start_date: date | None
    end_date: date | None
    source_url: str | None


class ExternalIdOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id_type: str
    id_value: str


class FigureOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    slug: str
    full_name: str
    branch: str
    bioguide_id: str | None
    is_active: bool
    roles: list[RoleOut]


class FigureDetailOut(FigureOut):
    external_ids: list[ExternalIdOut]


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    adapter: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    records_seen: int
    records_upserted: int
    error: str | None


def _current_role(figure: Figure):
    return figure.roles[-1] if figure.roles else None


def _initials(figure: Figure) -> str:
    first = (figure.first_name or figure.full_name or "?")[:1]
    last = (figure.last_name or "")[:1]
    return f"{first}{last}".upper()


def _card(figure: Figure, subtitle: str, portraits: set[str]) -> dict:
    return {
        "figure": figure,
        "portrait": bool(figure.bioguide_id) and figure.bioguide_id in portraits,
        "initials": _initials(figure),
        "subtitle": subtitle,
    }


@app.get("/", include_in_schema=False)
async def directory_page(
    request: Request, session: AsyncSession = Depends(get_async_session)
):
    figures = (
        await session.scalars(
            select(Figure)
            .options(selectinload(Figure.roles))
            .where(Figure.is_active)
            .order_by(Figure.id)
        )
    ).all()
    portraits = available_portraits()

    exec_lead: list[dict] = []
    cabinet: list[dict] = []
    chief: list[dict] = []
    associates: list[tuple] = []
    senators: list[tuple] = []
    representatives: list[tuple] = []

    for f in figures:
        role = _current_role(f)
        if role is None:
            continue
        acting = " (Acting)" if role.is_acting else ""
        if f.branch == "executive":
            if role.role_type in ("president", "vice_president"):
                exec_lead.append(_card(f, role.title, portraits))
            else:
                cabinet.append(_card(f, role.title + acting, portraits))
        elif f.branch == "judicial":
            if role.title.startswith("Chief"):
                chief.append(_card(f, role.title, portraits))
            else:
                associates.append((role.start_date, _card(f, role.title, portraits)))
        elif role.role_type == "sen":
            first_sen = min(
                (r.start_date for r in f.roles if r.role_type == "sen" and r.start_date),
                default=None,
            )
            senators.append(
                (first_sen, _card(f, f"U.S. Senator · {role.party or ''}", portraits))
            )
        elif role.role_type == "rep":
            representatives.append(
                (
                    role.district or 0,
                    _card(f, f"TX-{role.district} · {role.party or ''}", portraits),
                )
            )

    rep_cards = {d: c for d, c in representatives}
    house_cards = [
        {"vacant": d not in rep_cards, "district": d, "card": rep_cards.get(d)}
        for d in range(1, TX_HOUSE_SEATS + 1)
    ]

    return templates.TemplateResponse(
        request,
        "directory.html",
        {
            "exec_lead": exec_lead,
            "cabinet": cabinet,
            "justices": chief + [c for _, c in sorted(associates, key=lambda x: x[0])],
            "senators": [c for _, c in sorted(senators, key=lambda x: (x[0] is None, x[0]))],
            "house_cards": house_cards,
        },
    )


@app.get("/figures/{slug}", include_in_schema=False)
async def figure_page(
    slug: str, request: Request, session: AsyncSession = Depends(get_async_session)
):
    figure = await session.scalar(
        select(Figure)
        .options(selectinload(Figure.roles), selectinload(Figure.external_ids))
        .where(Figure.slug == slug)
    )
    if figure is None:
        raise HTTPException(status_code=404, detail=f"no figure with slug '{slug}'")

    vote_rows = (
        await session.execute(
            select(VoteCast, RollCall, Bill)
            .join(RollCall, RollCall.id == VoteCast.roll_call_id)
            .outerjoin(Bill, Bill.id == RollCall.bill_id)
            .where(VoteCast.figure_id == figure.id)
            .order_by(RollCall.vote_date.desc(), RollCall.roll_number.desc())
            .limit(15)
        )
    ).all()
    position_counts = dict(
        (
            await session.execute(
                select(VoteCast.position, func.count())
                .where(VoteCast.figure_id == figure.id)
                .group_by(VoteCast.position)
            )
        ).all()
    )
    statements = (
        await session.scalars(
            select(Statement)
            .where(Statement.figure_id == figure.id)
            .order_by(Statement.occurred_on.desc())
            .limit(10)
        )
    ).all()
    finance = (
        await session.scalars(
            select(FinanceSummary)
            .where(FinanceSummary.figure_id == figure.id)
            .order_by(FinanceSummary.cycle.desc(), FinanceSummary.fec_candidate_id)
        )
    ).all()
    source_rows = (
        await session.scalars(
            select(FinanceSource)
            .where(FinanceSource.figure_id == figure.id)
            .order_by(FinanceSource.cycle.desc(), FinanceSource.total.desc())
        )
    ).all()
    figure_promises = (
        await session.scalars(
            select(Promise)
            .options(selectinload(Promise.evidence))
            .where(Promise.figure_id == figure.id)
            .order_by(Promise.made_on.desc().nulls_last(), Promise.id.desc())
        )
    ).all()
    accountability = (
        await session.scalars(
            select(AccountabilityRecord)
            .where(AccountabilityRecord.figure_id == figure.id)
            .order_by(AccountabilityRecord.occurred_on.desc())
        )
    ).all()
    disclosures = (
        await session.scalars(
            select(DisclosureFiling)
            .where(DisclosureFiling.figure_id == figure.id)
            .order_by(DisclosureFiling.filing_date.desc().nulls_last())
            .limit(15)
        )
    ).all()
    disclosure_total = (
        await session.execute(
            select(func.count(DisclosureFiling.id)).where(
                DisclosureFiling.figure_id == figure.id
            )
        )
    ).scalar_one()

    # topic chips: statement tags + votes-through-bill-policy-areas, merged
    stmt_topic_rows = (
        await session.execute(
            select(Topic.id, Topic.name, func.count(StatementTopic.statement_id))
            .join(StatementTopic, StatementTopic.topic_id == Topic.id)
            .join(Statement, Statement.id == StatementTopic.statement_id)
            .where(Statement.figure_id == figure.id)
            .group_by(Topic.id, Topic.name)
        )
    ).all()
    vote_topic_rows = (
        await session.execute(
            select(Bill.policy_area, func.count(VoteCast.figure_id))
            .join(RollCall, RollCall.bill_id == Bill.id)
            .join(VoteCast, VoteCast.roll_call_id == RollCall.id)
            .where(VoteCast.figure_id == figure.id, Bill.policy_area.is_not(None))
            .group_by(Bill.policy_area)
        )
    ).all()
    topic_ids_by_name = dict(
        (await session.execute(select(Topic.name, Topic.id))).all()
    )
    chips: dict[int, dict] = {}
    for topic_id, name, n in stmt_topic_rows:
        chips[topic_id] = {"id": topic_id, "name": name, "statements": n, "votes": 0}
    for policy_area, n in vote_topic_rows:
        topic_id = topic_ids_by_name.get(policy_area)
        if topic_id is None:
            continue
        chips.setdefault(
            topic_id, {"id": topic_id, "name": policy_area, "statements": 0, "votes": 0}
        )["votes"] += n
    figure_topics = sorted(
        chips.values(), key=lambda c: c["statements"] + c["votes"], reverse=True
    )
    finance_sources: dict[int, list[FinanceSource]] = {}
    for src in source_rows:
        finance_sources.setdefault(src.cycle, []).append(src)

    # one chronological stream: said X (statements) next to did Y (votes)
    timeline = sorted(
        [
            {"kind": "vote", "date": rc.vote_date, "cast": vc, "roll_call": rc, "bill": bill}
            for vc, rc, bill in vote_rows
        ]
        + [{"kind": "statement", "date": s.occurred_on, "statement": s} for s in statements],
        key=lambda e: e["date"],
        reverse=True,
    )[:25]
    statement_total = (
        await session.execute(
            select(func.count(Statement.id)).where(Statement.figure_id == figure.id)
        )
    ).scalar_one()
    coverage = await coverage_summary(session, figure_slug=slug)
    portraits = available_portraits()

    return templates.TemplateResponse(
        request,
        "figure.html",
        {
            "figure": figure,
            "current_role": _current_role(figure),
            "portrait": bool(figure.bioguide_id) and figure.bioguide_id in portraits,
            "initials": _initials(figure),
            "stats": {
                "votes_total": sum(position_counts.values()),
                "yea": position_counts.get("yea", 0),
                "nay": position_counts.get("nay", 0),
                "not_voting": position_counts.get("not_voting", 0),
                "statements": statement_total,
            },
            "timeline": timeline,
            "finance": finance,
            "finance_sources": finance_sources,
            "promises": figure_promises,
            "promise_status_labels": PROMISE_STATUS_LABELS,
            "accountability": accountability,
            "record_type_labels": RECORD_TYPE_LABELS,
            "accusatory_types": ACCUSATORY_TYPES,
            "disclosures": disclosures,
            "disclosure_total": disclosure_total,
            "filing_type_labels": FILING_TYPE_LABELS,
            "figure_topics": figure_topics,
            "editing": is_editor(request),
            "coverage": coverage,
            "labels": SOURCE_TYPE_LABELS,
            "timeline_badges": TIMELINE_STATEMENT_BADGES,
        },
    )


@app.get("/figures/{slug}/topics/{topic_id}", include_in_schema=False)
async def figure_topic_page(
    slug: str,
    topic_id: int,
    request: Request,
    session: AsyncSession = Depends(get_async_session),
):
    figure = await session.scalar(
        select(Figure).options(selectinload(Figure.roles)).where(Figure.slug == slug)
    )
    topic = await session.get(Topic, topic_id)
    if figure is None or topic is None:
        raise HTTPException(status_code=404, detail="no such figure or topic")

    vote_rows = (
        await session.execute(
            select(VoteCast, RollCall, Bill)
            .join(RollCall, RollCall.id == VoteCast.roll_call_id)
            .join(Bill, Bill.id == RollCall.bill_id)
            .where(VoteCast.figure_id == figure.id, Bill.policy_area == topic.name)
            .order_by(RollCall.vote_date, RollCall.roll_number)
        )
    ).all()
    statement_rows = (
        await session.execute(
            select(Statement, StatementTopic)
            .join(StatementTopic, StatementTopic.statement_id == Statement.id)
            .where(
                Statement.figure_id == figure.id, StatementTopic.topic_id == topic.id
            )
            .order_by(Statement.occurred_on)
        )
    ).all()

    timeline = sorted(
        [
            {"kind": "vote", "date": rc.vote_date, "cast": vc, "roll_call": rc, "bill": bill}
            for vc, rc, bill in vote_rows
        ]
        + [
            {"kind": "statement", "date": s.occurred_on, "statement": s, "tag": tag}
            for s, tag in statement_rows
        ],
        key=lambda e: e["date"],
    )
    positions: dict[str, int] = {}
    for vc, _rc, _bill in vote_rows:
        positions[vc.position] = positions.get(vc.position, 0) + 1

    return templates.TemplateResponse(
        request,
        "figure_topic.html",
        {
            "figure": figure,
            "topic": topic,
            "timeline": timeline,
            "stats": {
                "votes": len(vote_rows),
                "yea": positions.get("yea", 0),
                "nay": positions.get("nay", 0),
                "other": len(vote_rows)
                - positions.get("yea", 0)
                - positions.get("nay", 0),
                "statements": len(statement_rows),
                "first": timeline[0]["date"] if timeline else None,
                "last": timeline[-1]["date"] if timeline else None,
            },
            "labels": SOURCE_TYPE_LABELS,
            "timeline_badges": TIMELINE_STATEMENT_BADGES,
        },
    )


@app.get("/search", include_in_schema=False)
async def search_page(
    request: Request,
    q: str | None = Query(default=None, max_length=500),
    figure: str | None = Query(default=None),
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
    session: AsyncSession = Depends(get_async_session),
):
    figures = (
        await session.scalars(
            select(Figure)
            .where(Figure.branch == "legislative", Figure.is_active)
            .order_by(Figure.last_name)
        )
    ).all()
    result = None
    if q:
        result = await _run_search(session, q, figure or None, date_from, date_to, limit=8)
    else:
        result = {"coverage": await coverage_summary(session)}
    return templates.TemplateResponse(
        request,
        "search.html",
        {
            "figures": figures,
            "q": q or "",
            "selected_figure": figure or "",
            "date_from": date_from,
            "date_to": date_to,
            "result": result,
            "labels": SOURCE_TYPE_LABELS,
        },
    )


@app.get("/api/search")
async def api_search(
    q: str = Query(min_length=2, max_length=500),
    figure: str | None = Query(default=None),
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
    limit: int = Query(default=8, ge=1, le=50),
    session: AsyncSession = Depends(get_async_session),
):
    result = await _run_search(session, q, figure, date_from, date_to, limit)
    result["matches"] = [asdict(m) for m in result["matches"]]
    return result


@app.get("/healthz")
async def healthz(session: AsyncSession = Depends(get_async_session)):
    await session.execute(text("SELECT 1"))
    runs = (
        await session.scalars(
            select(IngestionRun).order_by(IngestionRun.started_at.desc()).limit(10)
        )
    ).all()
    return {
        "db": "ok",
        "recent_ingestion_runs": [RunOut.model_validate(r).model_dump() for r in runs],
    }


@app.get("/api/figures", response_model=list[FigureOut])
async def list_figures(
    branch: str | None = Query(default=None, pattern="^(legislative|executive|judicial)$"),
    session: AsyncSession = Depends(get_async_session),
):
    stmt = (
        select(Figure)
        .options(selectinload(Figure.roles))
        .order_by(Figure.branch, Figure.last_name, Figure.full_name)
    )
    if branch:
        stmt = stmt.where(Figure.branch == branch)
    return (await session.scalars(stmt)).all()


@app.get("/api/figures/{slug}", response_model=FigureDetailOut)
async def get_figure(slug: str, session: AsyncSession = Depends(get_async_session)):
    figure = await session.scalar(
        select(Figure)
        .options(selectinload(Figure.roles), selectinload(Figure.external_ids))
        .where(Figure.slug == slug)
    )
    if figure is None:
        raise HTTPException(status_code=404, detail=f"no figure with slug '{slug}'")
    return figure
