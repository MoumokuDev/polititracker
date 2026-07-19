"""Topic tagging: machine-assigned navigation aids, clearly labeled as such.

Taxonomy = the CRS policy areas already attached to bills by Congress.gov
(factual, government-assigned). Votes reach topics through their bill's policy
area — no inference. Statements are tagged by two methods, most precise first:

  bill_reference        the statement's text cites a bill we know ("H.R. 9237")
                        → that bill's policy area, confidence 1.0. Factual.
  embedding_similarity  anchor-vs-chunk cosine similarity, thresholded, ≤2 tags.
                        Interpretive; stored with its similarity as confidence.

Every tag stores its method, and the UI presents tags as machine navigation
aids, never as part of the record. Recomputed in full each run (cheap at
corpus scale; threshold changes apply retroactively instead of leaving stale
tags).
"""

import logging
import re

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from truthtracker.config import get_settings
from truthtracker.ingestion.base import ingestion_run
from truthtracker.models import Bill, Statement, StatementChunk, StatementTopic, Topic
from truthtracker.search.embedder import embed_query

log = logging.getLogger(__name__)

ADAPTER = "topic_tagger"
METHOD_EMBED = "embedding_similarity_v1"
METHOD_BILLREF = "bill_reference"
MAX_EMBED_TOPICS = 2

_BILL_REF = re.compile(
    r"\b(H\.\s?R\.|H\.\s?J\.\s?Res\.|H\.\s?Con\.\s?Res\.|H\.\s?Res\.|"
    r"S\.\s?J\.\s?Res\.|S\.\s?Con\.\s?Res\.|S\.\s?Res\.|S\.)\s?(\d{1,5})\b"
)


def _bill_type_code(prefix: str) -> str:
    return re.sub(r"[.\s]", "", prefix).lower()


def _tag_by_bill_reference(session: Session, congress: int) -> int:
    """Statements citing a known bill get that bill's policy area. Confidence 1.0."""
    bills = session.execute(
        select(Bill.bill_type, Bill.number, Bill.policy_area).where(
            Bill.congress == congress, Bill.policy_area.is_not(None)
        )
    ).all()
    policy_by_ref = {(t, n): p for t, n, p in bills}
    topic_by_name = {
        t.name: t.id for t in session.scalars(select(Topic)).all()
    }

    tags = 0
    statements = session.execute(
        select(Statement.id, Statement.utterance_text, Statement.heading).where(
            Statement.source_type.in_(("crec_floor", "crec_extension"))
        )
    ).all()
    for statement_id, text, heading in statements:
        searchable = f"{heading or ''}\n{text[:4000]}"
        seen_topics: set[int] = set()
        for m in _BILL_REF.finditer(searchable):
            key = (_bill_type_code(m.group(1)), int(m.group(2)))
            policy = policy_by_ref.get(key)
            topic_id = topic_by_name.get(policy) if policy else None
            if topic_id is None or topic_id in seen_topics:
                continue
            seen_topics.add(topic_id)
            session.add(
                StatementTopic(
                    statement_id=statement_id,
                    topic_id=topic_id,
                    confidence=1.0,
                    method=METHOD_BILLREF,
                )
            )
            tags += 1
    session.flush()
    return tags


def run(session: Session, threshold: float | None = None) -> dict:
    settings = get_settings()
    threshold = threshold if threshold is not None else settings.topic_tag_threshold
    with ingestion_run(session, ADAPTER) as run_row:
        # 1. taxonomy: every policy area Congress.gov has assigned to our bills
        names = session.scalars(
            select(Bill.policy_area).where(Bill.policy_area.is_not(None)).distinct()
        ).all()
        for name in names:
            if session.scalar(select(Topic).where(Topic.name == name)) is None:
                session.add(Topic(name=name))
        session.flush()
        topics = session.scalars(select(Topic).order_by(Topic.id)).all()

        # 2. clear all machine tags (both methods) — recomputed below; any
        # future manual tags are untouched
        session.execute(
            delete(StatementTopic).where(
                StatementTopic.method.in_((METHOD_EMBED, METHOD_BILLREF))
            )
        )
        session.flush()

        # precise first: explicit bill citations → the bill's policy area
        billref_tags = _tag_by_bill_reference(session, settings.current_congress)
        billref_pairs = set(
            session.execute(
                select(StatementTopic.statement_id, StatementTopic.topic_id).where(
                    StatementTopic.method == METHOD_BILLREF
                )
            ).all()
        )

        # 3. similarity fallback: score statements against topic anchors
        scores: dict[int, list[tuple[float, int]]] = {}
        for topic in topics:
            anchor = embed_query(f"Congressional policy topic: {topic.name}")
            distance = func.min(StatementChunk.embedding.cosine_distance(anchor))
            rows = session.execute(
                select(StatementChunk.statement_id, distance)
                .where(StatementChunk.embedding.is_not(None))
                .group_by(StatementChunk.statement_id)
            ).all()
            for statement_id, dist in rows:
                sim = 1.0 - float(dist)
                if sim >= threshold and (statement_id, topic.id) not in billref_pairs:
                    scores.setdefault(statement_id, []).append((sim, topic.id))

        embed_tags = 0
        for statement_id, candidates in scores.items():
            for sim, topic_id in sorted(candidates, reverse=True)[:MAX_EMBED_TOPICS]:
                session.add(
                    StatementTopic(
                        statement_id=statement_id,
                        topic_id=topic_id,
                        confidence=round(sim, 4),
                        method=METHOD_EMBED,
                    )
                )
                embed_tags += 1
        session.commit()

        tagged_statements = (
            session.execute(
                select(func.count(func.distinct(StatementTopic.statement_id)))
            )
        ).scalar_one()
        run_row.records_seen = tagged_statements
        run_row.records_upserted = billref_tags + embed_tags
        summary = {
            "adapter": ADAPTER,
            "topics": len(topics),
            "statements_tagged": tagged_statements,
            "bill_reference_tags": billref_tags,
            "embedding_tags": embed_tags,
            "threshold": threshold,
        }
        log.info("%s", summary)
        return summary
