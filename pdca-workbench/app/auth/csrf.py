"""Origin checks for browser writes; authentication still runs in dependencies."""
from __future__ import annotations

from urllib.parse import urlsplit

from starlette.requests import Request

from app.auth.security import KNOWLEDGE_REAUTH_COOKIE
from app.config import get_settings


def _origin(value: str) -> tuple[str, str, int] | None:
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
        ):
            return None
        return parsed.scheme, parsed.hostname.lower(), parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return None


def browser_write_is_trusted(request: Request) -> bool:
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return True
    headers = request.headers
    browser_headers = any(key in headers for key in ("origin", "referer", "sec-fetch-site", "sec-fetch-mode"))
    has_cookie = any(key in request.cookies for key in ("pdca_token", KNOWLEDGE_REAUTH_COOKIE))
    scheme, _, credential = headers.get("authorization", "").partition(" ")
    # Never let a forged Authorization header exempt cookie-authenticated writes.
    if scheme.lower() == "bearer" and credential.strip() and not has_cookie and not browser_headers:
        return True
    form = headers.get("content-type", "").split(";", 1)[0].lower() in {
        "application/x-www-form-urlencoded", "multipart/form-data", "text/plain",
    }
    if not (has_cookie or browser_headers or form):
        return True
    if headers.get("sec-fetch-site", "") not in {"", "same-origin", "same-site", "none"}:
        return False
    # An explicit Origin (including null) takes precedence over Referer.
    source = _origin(headers.get("origin", "") if "origin" in headers else headers.get("referer", ""))
    if source is None:
        return False
    settings = get_settings()
    trusted = {_origin(str(request.url)), _origin(getattr(settings, "workbench_base_url", ""))}
    if settings.secure_cookies:
        # TLS termination may leave the ASGI URL as HTTP; do not trust X-Forwarded-* here.
        trusted.add(_origin(f"https://{request.url.netloc}"))
    return source in trusted
