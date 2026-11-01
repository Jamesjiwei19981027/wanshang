from __future__ import annotations

import hashlib
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UsernameField
from django.core.cache import cache
from django.http import HttpResponseForbidden
from django.shortcuts import render

from .attribution import trusted_client_ip

PUBLIC_FORM_PATHS = frozenset({
    "/api/inquiries",
    "/api/contact",
    "/api/contact-messages",
    "/api/v1/inquiries",
    "/api/v1/contact",
    "/api/v1/contact-messages",
})


def mark_admin_login_succeeded(sender, request, **kwargs) -> None:
    """Record a real login on this request, not an existing authenticated session."""
    if (
        request is not None
        and request.method == "POST"
        and request.path_info.rstrip("/") == "/admin/login"
    ):
        request._catalog_admin_login_succeeded = True


class AdminLoginRateLimitMiddleware:
    """Limit failed admin logins by IP and by each target username at that IP."""

    def __init__(self, get_response):
        self.get_response = get_response

    @staticmethod
    def _cache_key(request) -> str:
        ident = trusted_client_ip(request) or "unknown"
        digest = hashlib.sha256(ident.encode("utf-8")).hexdigest()
        # Old Redis counters may already have lost their expiry in the
        # EXISTS/INCR race. Do not carry a permanent legacy lock into this fix.
        return f"admin-login-failures:v2:{digest}"

    @classmethod
    def _username_cache_key(cls, request) -> str | None:
        user_model = get_user_model()
        username_field = user_model._meta.get_field(user_model.USERNAME_FIELD)
        # Match AuthenticationForm's stripping, NFKC normalization and its
        # protection against normalizing inputs longer than the model permits.
        username = UsernameField(max_length=username_field.max_length or 254).to_python(
            request.POST.get("username", "")
        )
        if not username:
            return None
        digest = hashlib.sha256(username.encode("utf-8")).hexdigest()
        return f"{cls._cache_key(request)}:user:{digest}"

    @staticmethod
    def _reserve_attempt(key: str) -> bool:
        # Redis (production) and LocMemCache (tests) provide atomic add/incr.
        # Reserve before password verification: counting completed failures lets
        # an arbitrary number of simultaneous guesses pass the same check.
        for _ in range(3):
            if cache.add(key, 1, timeout=settings.ADMIN_LOGIN_FAILURE_WINDOW):
                return True
            try:
                return cache.incr(key) <= settings.ADMIN_LOGIN_MAX_FAILURES
            except ValueError:
                # The bucket expired between add and incr. Retry add rather
                # than set, which could overwrite another request's reservation.
                continue
        return False

    @staticmethod
    def _throttled_response(request):
        response = render(
            request,
            "admin/login_throttled.html",
            {
                "failure_window": settings.ADMIN_LOGIN_FAILURE_WINDOW,
            },
            status=429,
        )
        response["Retry-After"] = str(settings.ADMIN_LOGIN_FAILURE_WINDOW)
        return response

    def __call__(self, request):
        is_login_attempt = (
            request.method == "POST"
            and request.path_info.rstrip("/") == "/admin/login"
        )
        if not is_login_attempt:
            return self.get_response(request)

        key = self._cache_key(request)
        failures = int(cache.get(key, 0) or 0)
        if failures >= settings.ADMIN_LOGIN_MAX_FAILURES:
            return self._throttled_response(request)

        # Check the IP bucket before reading POST data. The second bucket keeps
        # a staff member's successful reauthentication from erasing guesses
        # against another account, without locking that account at other IPs.
        username_key = self._username_cache_key(request)
        if (
            username_key
            and int(cache.get(username_key, 0) or 0) >= settings.ADMIN_LOGIN_MAX_FAILURES
        ):
            return self._throttled_response(request)

        if not self._reserve_attempt(key):
            return self._throttled_response(request)
        if username_key and not self._reserve_attempt(username_key):
            return self._throttled_response(request)

        request._catalog_admin_login_succeeded = False
        response = self.get_response(request)
        if request._catalog_admin_login_succeeded:
            cache.delete(key)
            if username_key:
                cache.delete(username_key)
        return response


def _origin_allowed(value: str, request) -> bool:
    if any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value):
        return False
    try:
        parsed = urlsplit(value)
        # Reading .port rejects invalid or out-of-range port numbers.
        _ = parsed.port
    except ValueError:
        return False
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or value != f"{parsed.scheme}://{parsed.netloc}"
    ):
        return False
    if value in settings.CSRF_TRUSTED_ORIGINS:
        return True
    return value == f"{request.scheme}://{request.get_host()}"


# Tokens that identify mainstream browser engines/brands. Used only as a
# heuristic to tell browsers apart from API clients when Origin is absent.
BROWSER_UA_MARKERS = (
    "mozilla",
    "chrome",
    "safari",
    "firefox",
    "edge",
    "applewebkit",
    "msie",
    "trident",
    "opera",
    "edg/",
)


def _looks_like_browser(user_agent: str) -> bool:
    normalized = user_agent.lower()
    return any(marker in normalized for marker in BROWSER_UA_MARKERS)


class PublicFormOriginMiddleware:
    """
    Reject cross-site browser POSTs to public write endpoints.

    DRF does not enforce CSRF on anonymous JSON/multipart posts; this restores
    same-origin protection for RFQ and contact submissions without breaking
    same-site fetches from the configured frontend origins.

    Non-browser clients (curl, server-to-server jobs, native apps) often omit
    Origin entirely and stay allowed. A browser-looking User-Agent without
    Origin is rejected instead, because real browsers always send Origin on
    cross-site POST.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method != "POST":
            return self.get_response(request)
        if (request.path_info.rstrip("/") or "/") not in PUBLIC_FORM_PATHS:
            return self.get_response(request)

        # Browsers always send Origin on cross-site POST. Referer is ignored
        # because legitimate referrers can be external product/campaign pages.
        origin = request.headers.get("Origin", "")
        if not origin:
            # Missing Origin is normal for non-browser clients and must keep
            # working; a browser UA without Origin is a red flag.
            if _looks_like_browser(request.headers.get("User-Agent", "")):
                return HttpResponseForbidden(
                    "Browser submissions must include a valid Origin header."
                )
            return self.get_response(request)
        if not _origin_allowed(origin, request):
            return HttpResponseForbidden("Cross-site form submissions are not allowed.")
        return self.get_response(request)
