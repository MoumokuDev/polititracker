"""Hybrid claim-to-utterance retrieval.

Two legs — pgvector cosine similarity and Postgres websearch full-text — fused
with Reciprocal Rank Fusion. No generative model anywhere: the pipeline ranks
existing verbatim chunks, it never writes text. Results always ship with
corpus-coverage metadata so "no match" can be reported honestly.
"""

from dataclasses import dataclass
from datetime import date

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from truthtracker.models import Figure, SourcePackage, Statement, StatementChunk

CANDIDATES_PER_LEG = 30
RRF_K = 60


@dataclass
class SearchMatch:
    statement_id: int
    figure_slug: str
    figure_name: str
    occurred_on: date
    source_type: str
    source_url: str
    heading: str | None
    confidence: float
    attribution_method: str | None
    chunk_text: str
    similarity: float | None  # cosine similarity if the vector leg found it
    lexical_hit: bool
    rrf_score: float


def _apply_filters(
    stmt: Select,
    figure_slug: str | None,
    date_from: date | None,
    date_to: date | None,
) -> Select:
    if figure_slug:
        stmt = stmt.where(Figure.slug == figure_slug)
    if date_from:
        stmt = stmt.where(Statement.occurred_on >= date_from)
    if date_to:
        stmt = stmt.where(Statement.occurred_on <= date_to)
    return stmt


async def hybrid_search(
    session: AsyncSession,
    query_text: str,
    query_vector: list[float],
    *,
    figure_slug: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = 8,
) -> list[SearchMatch]:
    base = (
        select(StatementChunk, Statement, Figure)
        .join(Statement, Statement.id == StatementChunk.statement_id)
        .join(Figure, Figure.id == Statement.figure_id)
    )
    base = _apply_filters(base, figure_slug, date_from, date_to)

    distance = StatementChunk.embedding.cosine_distance(query_vector)
    vector_rows = (
        await session.execute(
            base.add_columns(distance.label("distance"))
            .where(StatementChunk.embedding.is_not(None))
            .order_by(distance)
            .limit(CANDIDATES_PER_LEG)
        )
    ).all()

    tsquery = func.websearch_to_tsquery("english", query_text)
    rank = func.ts_rank(StatementChunk.tsv, tsquery)
    lexical_rows = (
        await session.execute(
            base.add_columns(rank.label("rank"))
            .where(StatementChunk.tsv.op("@@")(tsquery))
            .order_by(rank.desc())
            .limit(CANDIDATES_PER_LEG)
        )
    ).all()

    # Reciprocal Rank Fusion over chunk ids
    fused: dict[int, dict] = {}
    for leg_rank, row in enumerate(vector_rows):
        chunk = row[0]
        entry = fused.setdefault(chunk.id, {"row": row, "score": 0.0, "sim": None, "lex": False})
        entry["score"] += 1.0 / (RRF_K + leg_rank + 1)
        entry["sim"] = 1.0 - float(row[-1])  # cosine distance → similarity
    for leg_rank, row in enumerate(lexical_rows):
        chunk = row[0]
        entry = fused.setdefault(chunk.id, {"row": row, "score": 0.0, "sim": None, "lex": False})
        entry["score"] += 1.0 / (RRF_K + leg_rank + 1)
        entry["lex"] = True

    # keep the best-scoring chunk per statement
    best_per_statement: dict[int, dict] = {}
    for entry in fused.values():
        sid = entry["row"][1].id
        if sid not in best_per_statement or entry["score"] > best_per_statement[sid]["score"]:
            best_per_statement[sid] = entry

    ranked = sorted(best_per_statement.values(), key=lambda e: e["score"], reverse=True)[:limit]
    matches = []
    for entry in ranked:
        chunk, statement, figure = entry["row"][0], entry["row"][1], entry["row"][2]
        matches.append(
            SearchMatch(
                statement_id=statement.id,
                figure_slug=figure.slug,
                figure_name=figure.full_name,
                occurred_on=statement.occurred_on,
                source_type=statement.source_type,
                source_url=statement.source_url,
                heading=statement.heading,
                confidence=statement.confidence,
                attribution_method=statement.attribution_method,
                chunk_text=chunk.chunk_text,
                similarity=entry["sim"],
                lexical_hit=entry["lex"],
                rrf_score=entry["score"],
            )
        )
    return matches


COVERAGE_SOURCE_LABELS = {
    "govinfo_crec": "Congressional Record daily issues",
    "courtlistener_scotus": "Supreme Court opinion clusters",
    "federal_register": "presidential documents (Federal Register)",
}


async def coverage_summary(
    session: AsyncSession,
    *,
    figure_slug: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict:
    """What the corpus actually covers, per source — shipped with every response."""
    pkg = (
        select(
            SourcePackage.adapter,
            func.count(SourcePackage.id),
            func.min(SourcePackage.issue_date),
            func.max(SourcePackage.issue_date),
        )
        .where(SourcePackage.status == "ingested")
        .group_by(SourcePackage.adapter)
        .order_by(SourcePackage.adapter)
    )
    if date_from:
        pkg = pkg.where(SourcePackage.issue_date >= date_from)
    if date_to:
        pkg = pkg.where(SourcePackage.issue_date <= date_to)
    sources = [
        {
            "source": COVERAGE_SOURCE_LABELS.get(adapter, adapter),
            "units": count,
            "first": first,
            "last": last,
        }
        for adapter, count, first, last in (await session.execute(pkg)).all()
    ]

    stmt_count_q = select(func.count(Statement.id)).join(
        Figure, Figure.id == Statement.figure_id
    )
    stmt_count_q = _apply_filters(stmt_count_q, figure_slug, date_from, date_to)
    statements = (await session.execute(stmt_count_q)).scalar_one()

    failed_q = select(func.count(SourcePackage.id)).where(SourcePackage.status == "failed")
    failed = (await session.execute(failed_q)).scalar_one()

    return {
        "sources": sources,
        "failed_packages": failed,
        "statements_in_scope": statements,
        "note": (
            "Coverage is limited to the ingested units listed here. "
            "Absence of a match is not evidence a statement was never made."
        ),
    }
