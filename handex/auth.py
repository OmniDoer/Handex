from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Annotated, Optional

from fastapi import Cookie, HTTPException, Request, status
from fastapi.responses import RedirectResponse

from .config import settings


COOKIE_NAME = "handex_auth"


def auth_enabled() -> bool:
    return bool(settings.admin_password)


def expected_cookie() -> str:
    return hmac.new(
        settings.secret_key.encode("utf-8"),
        b"handex-auth-v1",
        hashlib.sha256,
    ).hexdigest()


def verify_password(password: str) -> bool:
    if not auth_enabled():
        return True
    return secrets.compare_digest(password, settings.admin_password)


def is_authorized(cookie_value: Optional[str]) -> bool:
    if not auth_enabled():
        return True
    if not cookie_value:
        return False
    return secrets.compare_digest(cookie_value, expected_cookie())


def require_auth(handex_auth: Annotated[Optional[str], Cookie()] = None) -> None:
    if not is_authorized(handex_auth):
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/login"})


def login_response(next_url: str = "/") -> RedirectResponse:
    response = RedirectResponse(next_url or "/", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        COOKIE_NAME,
        expected_cookie(),
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=60 * 60 * 24 * 30,
    )
    return response


def logout_response() -> RedirectResponse:
    response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(COOKIE_NAME)
    return response


def request_next_url(request: Request) -> str:
    value = request.query_params.get("next") or "/"
    if not value.startswith("/"):
        return "/"
    return value
