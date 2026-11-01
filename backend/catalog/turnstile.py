from __future__ import annotations

import hashlib
import hmac
import json
import logging
from urllib.error import URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.cache import cache

from .attribution import trusted_client_ip

logger = logging.getLogger(__name__)


class TurnstileVerificationError(ValueError):
    pass


def verify_turnstile_token(token: str, request, expected_action: str) -> None:
    """
    Verify a single-use Cloudflare Turnstile token and its form action.

    Development may skip verification only when no secret is configured and
    TURNSTILE_REQUIRED is false. Required environments always fail closed.
    """
    secret = settings.TURNSTILE_SECRET_KEY.strip()
    required = settings.TURNSTILE_REQUIRED
    token = str(token or "").strip()
    if not secret and not required:
        return
    if not secret:
        raise TurnstileVerificationError("Form verification is temporarily unavailable.")
    if not token:
        raise TurnstileVerificationError("Complete the verification challenge and try again.")
    if len(token) > 2048:
        raise TurnstileVerificationError("The verification token is invalid.")

    payload = {
        "secret": secret,
        "response": token,
        "remoteip": trusted_client_ip(request),
    }
    request_data = Request(
        settings.TURNSTILE_VERIFY_URL,
        data=urlencode(payload).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    try:
        # The destination is a fixed HTTPS Cloudflare endpoint, not user input.
        with urlopen(  # nosec B310
            request_data,
            timeout=settings.TURNSTILE_TIMEOUT,
        ) as response:
            result = json.loads(response.read(65_536).decode("utf-8"))
    except (OSError, URLError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        logger.warning("Turnstile verification request failed", exc_info=exc)
        raise TurnstileVerificationError(
            "Form verification is temporarily unavailable. Please try again."
        ) from exc

    if not isinstance(result, dict) or result.get("success") is not True:
        error_codes = result.get("error-codes", []) if isinstance(result, dict) else []
        logger.info("Turnstile rejected a form token", extra={"error_codes": error_codes})
        raise TurnstileVerificationError(
            "Verification failed or expired. Refresh the challenge and try again."
        )
    if result.get("action") != expected_action:
        logger.warning(
            "Turnstile action mismatch",
            extra={"expected_action": expected_action, "received_action": result.get("action")},
        )
        raise TurnstileVerificationError("The verification token is not valid for this form.")
    if settings.DJANGO_ENV == "production":
        # A shared widget key may also be enabled on preview or other sites.
        # Bind successful tokens to this deployment's approved form hosts.
        allowed_hostnames = {
            urlsplit(origin).hostname for origin in settings.CORS_ALLOWED_ORIGINS
        } | {hostname.strip("[]").lower() for hostname in settings.ALLOWED_HOSTS}
        hostname = result.get("hostname")
        if not isinstance(hostname, str) or hostname.lower() not in allowed_hostnames:
            logger.warning("Turnstile hostname mismatch")
            raise TurnstileVerificationError("The verification token is not valid for this site.")

    # Enforce one-time use locally too, including concurrent successful responses
    # from Siteverify. Production uses shared Redis; never store the raw token.
    fingerprint = hmac.new(
        settings.SECRET_KEY.encode("utf-8"), token.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    try:
        claimed = cache.add(f"turnstile:used:v1:{fingerprint}", True, timeout=300)
    except Exception as exc:
        logger.warning("Turnstile replay protection is unavailable")
        raise TurnstileVerificationError(
            "Form verification is temporarily unavailable. Please try again."
        ) from exc
    if not claimed:
        raise TurnstileVerificationError(
            "This verification token has already been used. Refresh the challenge and try again."
        )
