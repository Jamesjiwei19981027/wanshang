"""Container connection settings that preserve credentials as opaque values."""

from collections.abc import Mapping
from urllib.parse import quote

from django.core.exceptions import ImproperlyConfigured


def _port(env: Mapping[str, str], name: str, default: int) -> str:
    raw = env.get(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ImproperlyConfigured(f"{name} must be a port from 1 to 65535.") from exc
    if not raw.isascii() or not raw.isdecimal() or not 1 <= value <= 65535:
        raise ImproperlyConfigured(f"{name} must be a port from 1 to 65535.")
    return str(value)


def postgres_database_from_components(env: Mapping[str, str]) -> dict | None:
    host = env.get("DJANGO_DB_HOST", "").strip()
    if not host:
        return None
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env.get("DJANGO_DB_NAME", "trailer_parts"),
        "USER": env.get("DJANGO_DB_USER", "trailer"),
        "PASSWORD": env.get("DJANGO_DB_PASSWORD", ""),
        "HOST": host,
        "PORT": _port(env, "DJANGO_DB_PORT", 5432),
        "OPTIONS": {"connect_timeout": 5},
    }


def redis_url_from_components(env: Mapping[str, str], database: int) -> str:
    host = env.get("DJANGO_REDIS_HOST", "").strip()
    if not host:
        return ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = _port(env, "DJANGO_REDIS_PORT", 6379)
    username = quote(env.get("DJANGO_REDIS_USER", ""), safe="")
    password = quote(env.get("DJANGO_REDIS_PASSWORD", ""), safe="")
    authentication = f"{username}:{password}@" if username or password else ""
    return f"redis://{authentication}{host}:{port}/{database}"
