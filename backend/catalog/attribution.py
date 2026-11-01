from __future__ import annotations

import hashlib
import hmac
from urllib.parse import urlparse

from django.conf import settings
from django.utils import timezone
from rest_framework.settings import api_settings

from .models import AnalyticsEvent, VisitSession


def trusted_client_ip(request) -> str:
    """Use the same trusted-proxy interpretation as DRF throttling."""
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    remote_addr = request.META.get("REMOTE_ADDR", "")
    num_proxies = api_settings.NUM_PROXIES
    if num_proxies is not None:
        if num_proxies == 0 or x_forwarded_for is None:
            return remote_addr
        addresses = [address.strip() for address in x_forwarded_for.split(",")]
        return addresses[-min(num_proxies, len(addresses))]
    return "".join(x_forwarded_for.split()) if x_forwarded_for else remote_addr


def hash_client_ip(request) -> str:
    raw_ip = trusted_client_ip(request)
    if not raw_ip:
        return ""
    return hmac.new(
        settings.SECRET_KEY.encode(),
        raw_ip.encode(),
        hashlib.sha256,
    ).hexdigest()


def _text(value: object, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _derive_source(utm_source: str, referrer: str) -> str:
    if utm_source:
        return utm_source[:120]
    if referrer:
        try:
            hostname = urlparse(referrer).hostname
        except ValueError:
            hostname = None
        if hostname:
            return hostname[:120]
    return "direct"


def _trusted_geo(request) -> tuple[str, str]:
    if not settings.BEHIND_PROXY:
        return "", ""
    country_header = settings.GEOIP_COUNTRY_HEADER
    city_header = settings.GEOIP_CITY_HEADER
    return (
        _text(request.META.get(country_header, ""), 100)
        if country_header
        else "",
        _text(request.META.get(city_header, ""), 100)
        if city_header
        else "",
    )


def _user_agent_dimensions(user_agent: str) -> tuple[str, str, str]:
    normalized = user_agent.lower()
    if not normalized:
        return "", "", ""
    if any(marker in normalized for marker in ("bot", "spider", "crawler")):
        device = "bot"
    elif any(marker in normalized for marker in ("ipad", "tablet")):
        device = "tablet"
    elif any(marker in normalized for marker in ("mobile", "iphone", "android")):
        device = "mobile"
    else:
        device = "desktop"

    if "edg/" in normalized:
        browser = "Edge"
    elif "opr/" in normalized or "opera" in normalized:
        browser = "Opera"
    elif "chrome/" in normalized or "crios/" in normalized:
        browser = "Chrome"
    elif "firefox/" in normalized or "fxios/" in normalized:
        browser = "Firefox"
    elif "safari/" in normalized:
        browser = "Safari"
    else:
        browser = "Other"

    if "windows" in normalized:
        operating_system = "Windows"
    elif "android" in normalized:
        operating_system = "Android"
    elif any(marker in normalized for marker in ("iphone", "ipad", "ios")):
        operating_system = "iOS"
    elif "mac os" in normalized or "macintosh" in normalized:
        operating_system = "macOS"
    elif "linux" in normalized:
        operating_system = "Linux"
    else:
        operating_system = "Other"
    return device, browser, operating_system


def upsert_visit_session(
    session_key: str,
    payload: dict,
    request,
) -> VisitSession:
    now = timezone.now()
    referrer = _text(
        payload.get("referrer_url") or request.headers.get("Referer", ""),
        1000,
    )
    first_landing = _text(
        payload.get("first_landing_page") or payload.get("source_url"),
        500,
    )
    last_landing = _text(
        payload.get("last_landing_page")
        or payload.get("source_url")
        or payload.get("first_landing_page"),
        500,
    )
    utm_source = _text(payload.get("utm_source"), 120)
    derived_source = _derive_source(utm_source, referrer)
    first_touch = _text(payload.get("first_touch_source"), 120) or derived_source
    last_touch = _text(payload.get("last_touch_source"), 120) or derived_source
    user_agent = _text(request.headers.get("User-Agent", ""), 1000)
    country, city = _trusted_geo(request)
    device_type, browser, operating_system = _user_agent_dimensions(user_agent)
    has_touch_update = any(
        _text(payload.get(field), 1000)
        for field in (
            "last_touch_source",
            "utm_source",
            "referrer_url",
        )
    )

    defaults = {
        "first_ip_hash": hash_client_ip(request),
        "country": country,
        "city": city,
        "first_touch_source": first_touch,
        "last_touch_source": last_touch,
        "utm_source": utm_source,
        "utm_medium": _text(payload.get("utm_medium"), 120),
        "utm_campaign": _text(payload.get("utm_campaign"), 150),
        "utm_term": _text(payload.get("utm_term"), 150),
        "utm_content": _text(payload.get("utm_content"), 150),
        "referrer_url": referrer,
        "first_landing_page": first_landing,
        "last_landing_page": last_landing,
        "user_agent": user_agent,
        "device_type": device_type,
        "browser": browser,
        "os": operating_system,
        "consent_status": _text(payload.get("consent_status"), 30)
        or VisitSession.ConsentStatus.UNKNOWN,
        "started_at": now,
        "last_seen_at": now,
    }
    session, created = VisitSession.objects.get_or_create(
        session_key=session_key,
        defaults=defaults,
    )
    if not created:
        update_fields = {"last_seen_at", "updated_at"}
        session.last_seen_at = now
        for field, value in {
            "last_touch_source": last_touch if has_touch_update else "",
            "last_landing_page": last_landing,
            "device_type": defaults["device_type"],
            "browser": defaults["browser"],
            "os": defaults["os"],
        }.items():
            if value:
                setattr(session, field, value)
                update_fields.add(field)
        consent_status = defaults["consent_status"]
        if consent_status != VisitSession.ConsentStatus.UNKNOWN:
            session.consent_status = consent_status
            update_fields.add("consent_status")
        for field in (
            "first_ip_hash",
            "country",
            "city",
            "first_touch_source",
            "utm_source",
            "utm_medium",
            "utm_campaign",
            "utm_term",
            "utm_content",
            "referrer_url",
            "first_landing_page",
            "user_agent",
        ):
            if not getattr(session, field) and defaults[field]:
                setattr(session, field, defaults[field])
                update_fields.add(field)
        session.save(update_fields=sorted(update_fields))

    AnalyticsEvent.objects.filter(
        session_id=session_key,
        visit_session__isnull=True,
    ).update(visit_session=session)
    return session


def build_submission_attribution(
    session_key: str,
    payload: dict,
    request,
) -> tuple[VisitSession | None, dict[str, object]]:
    session = upsert_visit_session(session_key, payload, request) if session_key else None
    request_referrer = _text(
        payload.get("referrer_url") or request.headers.get("Referer", ""),
        1000,
    )
    referrer = session.referrer_url if session and session.referrer_url else request_referrer
    source_url = _text(payload.get("source_url"), 500)
    direct_source = _derive_source(_text(payload.get("utm_source"), 120), referrer)

    attribution: dict[str, object] = {
        "session": session,
        "referrer": referrer,
        "first_touch_source": (
            session.first_touch_source
            if session
            else _text(payload.get("first_touch_source"), 120) or direct_source
        ),
        "last_touch_source": (
            session.last_touch_source
            if session
            else _text(payload.get("last_touch_source"), 120) or direct_source
        ),
        "utm_source": _text(payload.get("utm_source"), 120)
        or (session.utm_source if session else ""),
        "utm_medium": _text(payload.get("utm_medium"), 120)
        or (session.utm_medium if session else ""),
        "utm_campaign": _text(payload.get("utm_campaign"), 150)
        or (session.utm_campaign if session else ""),
        "utm_term": _text(payload.get("utm_term"), 150)
        or (session.utm_term if session else ""),
        "utm_content": _text(payload.get("utm_content"), 150)
        or (session.utm_content if session else ""),
        "first_landing_page": (
            session.first_landing_page if session else source_url
        ),
        "last_landing_page": (
            session.last_landing_page if session else source_url
        ),
        "user_agent": _text(request.headers.get("User-Agent", ""), 1000),
        "ip_hash": hash_client_ip(request),
    }
    if session:
        converted_at = timezone.now()
        VisitSession.objects.filter(pk=session.pk).update(last_seen_at=converted_at)
        VisitSession.objects.filter(
            pk=session.pk,
            converted_at__isnull=True,
        ).update(converted_at=converted_at)
    return session, attribution
