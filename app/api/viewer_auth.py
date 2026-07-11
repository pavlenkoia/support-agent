from __future__ import annotations

import base64
import hashlib
import hmac
import time
from dataclasses import dataclass

from fastapi import Cookie, Depends, HTTPException, Response, status

from app.core.config import settings


AUTH_REQUIRED_DETAIL = "Viewer authentication required."
INVALID_KEY_DETAIL = "Invalid viewer access key."
MISCONFIGURED_DETAIL = "Viewer authentication is misconfigured."


@dataclass(slots=True)
class ViewerAuthContext:
    authenticated: bool


def _require_auth_key() -> str:
    key = settings.viewer_auth_key
    if not key:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=MISCONFIGURED_DETAIL)
    return key


def _sign_payload(payload: str) -> str:
    digest = hmac.new(_require_auth_key().encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _parse_signed_value(value: str | None) -> bool:
    if not value:
        return False

    try:
        expires_at_text, signature = value.split(".", 1)
        expires_at = int(expires_at_text)
    except (ValueError, AttributeError):
        return False

    if expires_at < int(time.time()):
        return False

    expected_signature = _sign_payload(expires_at_text)
    return hmac.compare_digest(signature, expected_signature)


def _build_signed_cookie_value() -> str:
    max_age = max(settings.viewer_auth_session_days, 1) * 24 * 60 * 60
    expires_at = int(time.time()) + max_age
    payload = str(expires_at)
    return f"{payload}.{_sign_payload(payload)}"


def set_viewer_auth_cookie(response: Response) -> None:
    max_age = max(settings.viewer_auth_session_days, 1) * 24 * 60 * 60
    response.set_cookie(
        key=settings.viewer_auth_cookie_name,
        value=_build_signed_cookie_value(),
        max_age=max_age,
        httponly=True,
        samesite="lax",
        secure=settings.viewer_auth_cookie_secure,
        path="/",
    )


def clear_viewer_auth_cookie(response: Response) -> None:
    response.delete_cookie(
        key=settings.viewer_auth_cookie_name,
        httponly=True,
        samesite="lax",
        secure=settings.viewer_auth_cookie_secure,
        path="/",
    )


def verify_viewer_key(key: str) -> None:
    expected_key = _require_auth_key()
    if not hmac.compare_digest(key, expected_key):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=INVALID_KEY_DETAIL)


def get_viewer_auth_context(
    viewer_auth_cookie: str | None = Cookie(default=None, alias=settings.viewer_auth_cookie_name),
) -> ViewerAuthContext:
    if not settings.viewer_auth_enabled:
        return ViewerAuthContext(authenticated=True)
    return ViewerAuthContext(authenticated=_parse_signed_value(viewer_auth_cookie))


def require_viewer_auth(context: ViewerAuthContext = Depends(get_viewer_auth_context)) -> ViewerAuthContext:
    if not context.authenticated:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=AUTH_REQUIRED_DETAIL)
    return context
