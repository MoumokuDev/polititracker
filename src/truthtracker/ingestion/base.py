"""Shared provenance plumbing for all source adapters.

Rules every adapter must follow:
- every parsed record FKs to the source_fetch row its raw payload lives in;
- every run writes an ingestion_run row, success OR failure — fail loudly;
- upserts are idempotent, keyed on source-native IDs.
"""

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from truthtracker.models import IngestionRun, SourceFetch


def json_safe(payload: dict) -> dict:
    """Round-trip through json so dates etc. become strings before JSONB storage.

    Also strips NUL escapes — Postgres cannot store \\u0000 in JSONB/text, and
    some upstream documents (e.g. Federal Register raw text) contain them.
    """
    return json.loads(json.dumps(payload, default=str).replace("\\u0000", ""))


def canonical_hash(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def record_fetch(
    session: Session,
    *,
    adapter: str,
    native_id: str,
    source_url: str,
    payload: dict,
    source_version: str | None = None,
) -> SourceFetch:
    """Store a raw payload, deduplicated on (adapter, native_id, content_hash)."""
    payload = json_safe(payload)
    content_hash = canonical_hash(payload)
    existing = session.scalar(
        select(SourceFetch).where(
            SourceFetch.adapter == adapter,
            SourceFetch.native_id == native_id,
            SourceFetch.content_hash == content_hash,
        )
    )
    if existing is not None:
        return existing
    fetch = SourceFetch(
        adapter=adapter,
        native_id=native_id,
        source_url=source_url,
        payload=payload,
        content_hash=content_hash,
        source_version=source_version,
    )
    session.add(fetch)
    session.flush()
    return fetch


@contextmanager
def ingestion_run(session: Session, adapter: str) -> Iterator[IngestionRun]:
    """Record the run outcome no matter what; re-raise failures (fail loudly)."""
    run = IngestionRun(adapter=adapter, status="running")
    session.add(run)
    session.commit()
    run_id = run.id
    try:
        yield run
        run.status = "success"
        run.finished_at = datetime.now(UTC)
        session.commit()
    except Exception as exc:
        session.rollback()
        failed = session.get(IngestionRun, run_id)
        failed.status = "failure"
        failed.error = f"{type(exc).__name__}: {exc}"
        failed.finished_at = datetime.now(UTC)
        session.commit()
        raise
