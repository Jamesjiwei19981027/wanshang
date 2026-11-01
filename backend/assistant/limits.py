"""Daily question limits and the daily cost cap."""

from __future__ import annotations

import hashlib
import hmac
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.cache import cache
from django.db.models import Sum
from django.utils import timezone

from .models import AssistantSetting, Message


def visitor_hash(visitor_id: str) -> str:
    return hmac.new(settings.SECRET_KEY.encode(), f"assistant-visitor:{visitor_id}".encode(), hashlib.sha256).hexdigest()


def _seconds_until_midnight() -> int:
    now = timezone.localtime()
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(60, int((midnight - now).total_seconds()) + 60)


def _day_key(kind: str, identity: str) -> str:
    return f"assistant:limit:{kind}:{timezone.localdate():%Y%m%d}:{identity}"


def consume_question(setting: AssistantSetting, visitor: str, ip_hash: str) -> str | None:
    """Count one question; return the exhausted limit's name, or None."""
    checks = [("visitor", visitor, setting.visitor_daily_limit)]
    if ip_hash:
        checks.append(("ip", ip_hash, setting.ip_daily_limit))
    timeout = _seconds_until_midnight()
    counted: list[str] = []
    for kind, identity, limit in checks:
        key = _day_key(kind, identity)
        cache.add(key, 0, timeout)
        try:
            # Increment first so concurrent questions cannot both slip under.
            used = cache.incr(key)
        except ValueError:
            cache.set(key, 1, timeout)
            used = 1
        counted.append(key)
        if used > limit:
            for counted_key in counted:
                try:
                    cache.decr(counted_key)
                except ValueError:
                    pass
            return kind
    return None


def release_question(visitor: str, ip_hash: str) -> None:
    """Give a question back when the AI service failed before answering."""
    for kind, identity in (("visitor", visitor), ("ip", ip_hash)):
        if not identity:
            continue
        key = _day_key(kind, identity)
        try:
            if (cache.get(key) or 0) > 0:
                cache.decr(key)
        except ValueError:
            pass


def remaining_questions(setting: AssistantSetting, visitor: str) -> int:
    used = cache.get(_day_key("visitor", visitor)) or 0
    return max(0, setting.visitor_daily_limit - used)


def cost_today() -> Decimal:
    start = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    return Message.objects.filter(created_at__gte=start).aggregate(total=Sum("cost"))["total"] or Decimal("0")


def cost_cap_reached(setting: AssistantSetting) -> bool:
    return setting.daily_cost_cap is not None and cost_today() >= setting.daily_cost_cap

