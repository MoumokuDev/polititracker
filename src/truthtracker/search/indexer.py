"""Chunk + embed statements into statement_chunk. Idempotent; ledger-logged."""

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from truthtracker.ingestion.base import ingestion_run
from truthtracker.models import Statement, StatementChunk
from truthtracker.search.chunking import chunk_text
from truthtracker.search.embedder import MODEL_NAME, embed_passages

log = logging.getLogger(__name__)

ADAPTER = "statement_indexer"
EMBED_BATCH = 64


def run(session: Session, embed_limit: int | None = None) -> dict:
    with ingestion_run(session, ADAPTER) as run_row:
        # 1. chunk statements that have no chunks yet
        unchunked = session.scalars(
            select(Statement).where(
                ~Statement.id.in_(select(StatementChunk.statement_id).distinct())
            )
        ).all()
        for stmt in unchunked:
            for i, piece in enumerate(chunk_text(stmt.utterance_text)):
                session.add(
                    StatementChunk(statement_id=stmt.id, chunk_index=i, chunk_text=piece)
                )
        session.commit()

        # 2. embed chunks missing embeddings
        pending = session.scalars(
            select(StatementChunk)
            .where(StatementChunk.embedding.is_(None))
            .order_by(StatementChunk.id)
            .limit(embed_limit)
        ).all()
        embedded = 0
        for i in range(0, len(pending), EMBED_BATCH):
            batch = pending[i : i + EMBED_BATCH]
            vectors = embed_passages([c.chunk_text for c in batch])
            for chunk, vector in zip(batch, vectors, strict=True):
                chunk.embedding = vector
                chunk.embedding_model = MODEL_NAME
            embedded += len(batch)
            session.commit()

        run_row.records_seen = len(unchunked)
        run_row.records_upserted = embedded
        summary = {
            "adapter": ADAPTER,
            "statements_chunked": len(unchunked),
            "chunks_embedded": embedded,
            "model": MODEL_NAME,
        }
        log.info("%s", summary)
        return summary
