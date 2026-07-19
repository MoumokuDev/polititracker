"""Promise tracking routes: records + attributed editorial assessment.

Editing endpoints are gated by the editor session (see api/auth.py): the
enable_editing master switch plus, when editor_password is set, a login.
"""

from datetime import UTC, date, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from truthtracker.api.auth import is_editor, require_editor
from truthtracker.config import get_settings
from truthtracker.db import get_async_session
from truthtracker.models import (
    PROMISE_STATUS_LABELS,
    Figure,
    Promise,
    PromiseEvidence,
    Statement,
)

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

EVIDENCE_KINDS = ("statement", "vote", "bill", "external")


def _valid_url(url: str) -> str:
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="source_url must be an http(s) URL")
    return url


async def _get_figure(session: AsyncSession, slug: str) -> Figure:
    figure = await session.scalar(select(Figure).where(Figure.slug == slug))
    if figure is None:
        raise HTTPException(status_code=404, detail=f"no figure with slug '{slug}'")
    return figure


async def _get_promise(session: AsyncSession, promise_id: int) -> Promise:
    promise = await session.scalar(
        select(Promise)
        .options(selectinload(Promise.evidence))
        .where(Promise.id == promise_id)
    )
    if promise is None:
        raise HTTPException(status_code=404, detail="no such promise")
    return promise


@router.get("/figures/{slug}/promises/new", include_in_schema=False)
async def promise_new_form(
    slug: str,
    request: Request,
    statement_id: int | None = None,
    session: AsyncSession = Depends(get_async_session),
):
    require_editor(request)
    figure = await _get_figure(session, slug)
    prefill = {"quote": "", "source_url": "", "made_on": "", "statement_id": ""}
    if statement_id is not None:
        stmt = await session.get(Statement, statement_id)
        if stmt is not None and stmt.figure_id == figure.id:
            prefill = {
                "quote": stmt.utterance_text,
                "source_url": stmt.source_url,
                "made_on": stmt.occurred_on.isoformat(),
                "statement_id": str(stmt.id),
            }
    return templates.TemplateResponse(
        request, "promise_new.html", {"figure": figure, "prefill": prefill}
    )


@router.post("/figures/{slug}/promises", include_in_schema=False)
async def promise_create(
    slug: str,
    request: Request,
    title: str = Form(...),
    quote: str = Form(...),
    source_url: str = Form(...),
    made_on: str = Form(""),
    statement_id: str = Form(""),
    session: AsyncSession = Depends(get_async_session),
):
    require_editor(request)
    figure = await _get_figure(session, slug)
    title, quote = title.strip(), quote.strip()
    if not title or not quote:
        raise HTTPException(status_code=400, detail="title and quote are required")

    linked_statement_id = None
    if statement_id.strip():
        stmt = await session.get(Statement, int(statement_id))
        if stmt is None or stmt.figure_id != figure.id:
            raise HTTPException(status_code=400, detail="statement does not belong to figure")
        if quote not in stmt.utterance_text:
            raise HTTPException(
                status_code=400,
                detail=(
                    "quote must be a verbatim substring of the linked statement — "
                    "edit the quote by trimming, not rewording"
                ),
            )
        linked_statement_id = stmt.id

    promise = Promise(
        figure_id=figure.id,
        title=title[:200],
        quote=quote,
        source_url=_valid_url(source_url),
        made_on=date.fromisoformat(made_on) if made_on.strip() else None,
        statement_id=linked_statement_id,
    )
    session.add(promise)
    await session.commit()
    return RedirectResponse(f"/promises/{promise.id}", status_code=303)


@router.get("/promises/{promise_id}", include_in_schema=False)
async def promise_detail(
    promise_id: int, request: Request, session: AsyncSession = Depends(get_async_session)
):
    promise = await _get_promise(session, promise_id)
    figure = await session.get(Figure, promise.figure_id)
    return templates.TemplateResponse(
        request,
        "promise_detail.html",
        {
            "promise": promise,
            "figure": figure,
            "status_labels": PROMISE_STATUS_LABELS,
            "evidence_kinds": EVIDENCE_KINDS,
            "editing": is_editor(request),
            "editor_name": get_settings().editor_name,
        },
    )


@router.post("/promises/{promise_id}/evidence", include_in_schema=False)
async def evidence_add(
    promise_id: int,
    request: Request,
    kind: str = Form(...),
    note: str = Form(...),
    source_url: str = Form(...),
    session: AsyncSession = Depends(get_async_session),
):
    require_editor(request)
    promise = await _get_promise(session, promise_id)
    if kind not in EVIDENCE_KINDS:
        raise HTTPException(status_code=400, detail=f"kind must be one of {EVIDENCE_KINDS}")
    if not note.strip():
        raise HTTPException(status_code=400, detail="a note describing the evidence is required")
    session.add(
        PromiseEvidence(
            promise_id=promise.id,
            kind=kind,
            note=note.strip(),
            source_url=_valid_url(source_url),
        )
    )
    await session.commit()
    return RedirectResponse(f"/promises/{promise.id}", status_code=303)


@router.post("/promises/{promise_id}/status", include_in_schema=False)
async def status_set(
    promise_id: int,
    request: Request,
    status: str = Form(...),
    assessment: str = Form(""),
    assessed_by: str = Form(""),
    session: AsyncSession = Depends(get_async_session),
):
    require_editor(request)
    promise = await _get_promise(session, promise_id)
    if status not in PROMISE_STATUS_LABELS:
        raise HTTPException(status_code=400, detail="unknown status")
    if status == "unassessed":
        promise.status = "unassessed"
        promise.assessment = None
        promise.assessed_by = None
        promise.assessed_at = None
    else:
        if not assessment.strip() or not assessed_by.strip():
            raise HTTPException(
                status_code=400,
                detail=(
                    "an assessment rationale and editor name are required — "
                    "a status is an editorial judgment and must be attributed"
                ),
            )
        promise.status = status
        promise.assessment = assessment.strip()
        promise.assessed_by = assessed_by.strip()[:120]
        promise.assessed_at = datetime.now(UTC)
    await session.commit()
    return RedirectResponse(f"/promises/{promise.id}", status_code=303)


@router.post("/promises/{promise_id}/delete", include_in_schema=False)
async def promise_delete(
    promise_id: int,
    request: Request,
    session: AsyncSession = Depends(get_async_session),
):
    require_editor(request)
    promise = await _get_promise(session, promise_id)
    figure = await session.get(Figure, promise.figure_id)
    for row in promise.evidence:
        await session.delete(row)
    await session.delete(promise)
    await session.commit()
    return RedirectResponse(f"/figures/{figure.slug}", status_code=303)


@router.get("/api/figures/{slug}/promises")
async def promises_json(slug: str, session: AsyncSession = Depends(get_async_session)):
    figure = await _get_figure(session, slug)
    promises = (
        await session.scalars(
            select(Promise)
            .options(selectinload(Promise.evidence))
            .where(Promise.figure_id == figure.id)
            .order_by(Promise.made_on.desc().nulls_last(), Promise.id.desc())
        )
    ).all()
    return [
        {
            "id": p.id,
            "title": p.title,
            "quote": p.quote,
            "made_on": p.made_on,
            "source_url": p.source_url,
            "status": p.status,
            "status_label": PROMISE_STATUS_LABELS[p.status],
            "assessment": p.assessment,
            "assessed_by": p.assessed_by,
            "assessed_at": p.assessed_at,
            "evidence": [
                {"kind": e.kind, "note": e.note, "source_url": e.source_url}
                for e in p.evidence
            ],
        }
        for p in promises
    ]
