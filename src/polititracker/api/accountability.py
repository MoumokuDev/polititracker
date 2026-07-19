"""Accountability record routes: append-only documented events, editor-curated.

Same editing gate as promises (see api/auth.py): enable_editing master switch
plus editor login when editor_password is set.
"""

from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from polititracker.api.auth import require_editor
from polititracker.db import get_async_session
from polititracker.models import RECORD_TYPE_LABELS, AccountabilityRecord, Figure

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


async def _get_figure(session: AsyncSession, slug: str) -> Figure:
    figure = await session.scalar(select(Figure).where(Figure.slug == slug))
    if figure is None:
        raise HTTPException(status_code=404, detail=f"no figure with slug '{slug}'")
    return figure


@router.get("/figures/{slug}/accountability/new", include_in_schema=False)
async def record_new_form(
    slug: str, request: Request, session: AsyncSession = Depends(get_async_session)
):
    require_editor(request)
    figure = await _get_figure(session, slug)
    return templates.TemplateResponse(
        request,
        "accountability_new.html",
        {"figure": figure, "type_labels": RECORD_TYPE_LABELS},
    )


@router.post("/figures/{slug}/accountability", include_in_schema=False)
async def record_create(
    slug: str,
    request: Request,
    record_type: str = Form(...),
    title: str = Form(...),
    description: str = Form(...),
    occurred_on: str = Form(...),
    source_url: str = Form(...),
    docket_number: str = Form(""),
    status_note: str = Form(""),
    session: AsyncSession = Depends(get_async_session),
):
    require_editor(request)
    figure = await _get_figure(session, slug)
    if record_type not in RECORD_TYPE_LABELS:
        raise HTTPException(status_code=400, detail="unknown record type")
    if not title.strip() or not description.strip():
        raise HTTPException(status_code=400, detail="title and description are required")
    source_url = source_url.strip()
    if not source_url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="source_url must be an http(s) URL")
    session.add(
        AccountabilityRecord(
            figure_id=figure.id,
            record_type=record_type,
            title=title.strip()[:250],
            description=description.strip(),
            occurred_on=date.fromisoformat(occurred_on),
            source_url=source_url,
            docket_number=docket_number.strip()[:100] or None,
            status_note=status_note.strip()[:300] or None,
        )
    )
    await session.commit()
    return RedirectResponse(f"/figures/{slug}", status_code=303)


@router.post("/accountability/{record_id}/delete", include_in_schema=False)
async def record_delete(
    record_id: int,
    request: Request,
    session: AsyncSession = Depends(get_async_session),
):
    require_editor(request)
    record = await session.get(AccountabilityRecord, record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="no such record")
    figure = await session.get(Figure, record.figure_id)
    await session.delete(record)
    await session.commit()
    return RedirectResponse(f"/figures/{figure.slug}", status_code=303)
