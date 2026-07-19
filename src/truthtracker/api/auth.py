"""Editor authentication: a single shared secret, HMAC-signed session cookie.

Modes, controlled by settings:
- enable_editing=False              → editing fully off (hard 403s).
- enable_editing=True, no password  → editing open (local development mode).
- enable_editing=True + password    → editing requires login at /login.

This is deliberately minimal single-editor auth for a self-hosted instance.
It is not multi-user account management.
"""

import hashlib
import hmac
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from truthtracker.config import get_settings

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

COOKIE_NAME = "tt_editor"
_TOKEN_PAYLOAD = "editor-session-v1"


def _signature() -> str:
    settings = get_settings()
    key = (settings.secret_key or settings.editor_password).encode()
    return hmac.new(key, _TOKEN_PAYLOAD.encode(), hashlib.sha256).hexdigest()


def is_editor(request: Request) -> bool:
    settings = get_settings()
    if not settings.enable_editing:
        return False
    if not settings.editor_password:
        return True  # development mode: editing open, as before
    token = request.cookies.get(COOKIE_NAME, "")
    return hmac.compare_digest(token, _signature())


def require_editor(request: Request) -> None:
    if not get_settings().enable_editing:
        raise HTTPException(status_code=403, detail="editing is disabled on this instance")
    if not is_editor(request):
        raise HTTPException(
            status_code=403, detail="editor login required (visit /login)"
        )


@router.get("/login", include_in_schema=False)
async def login_form(request: Request):
    return templates.TemplateResponse(
        request, "login.html", {"failed": False, "editor": is_editor(request)}
    )


@router.post("/login", include_in_schema=False)
async def login(request: Request, password: str = Form(...)):
    settings = get_settings()
    if not settings.enable_editing or not settings.editor_password:
        raise HTTPException(status_code=403, detail="password login is not enabled")
    if not hmac.compare_digest(password, settings.editor_password):
        return templates.TemplateResponse(
            request, "login.html", {"failed": True, "editor": False}, status_code=401
        )
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        COOKIE_NAME,
        _signature(),
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        max_age=60 * 60 * 24 * 30,
    )
    return response


@router.post("/logout", include_in_schema=False)
async def logout():
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(COOKIE_NAME)
    return response
