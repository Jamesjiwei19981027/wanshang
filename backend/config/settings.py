from __future__ import annotations

import math
import os
from ipaddress import ip_address
from pathlib import Path
import re
from urllib.parse import parse_qsl, unquote, urlparse

from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.validators import validate_email
from corsheaders.defaults import default_headers

from .connections import postgres_database_from_components, redis_url_from_components

BASE_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BASE_DIR.parent


def load_env_file() -> None:
    """Load local values without adding a runtime dependency for dotenv."""
    env_file = PROJECT_DIR / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env_file()


def positive_float_from_env(name: str, default: str) -> float:
    raw_value = os.getenv(name, default).strip()
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise ImproperlyConfigured(
            f"{name} must be a positive finite number."
        ) from exc
    if not math.isfinite(value) or value <= 0:
        raise ImproperlyConfigured(
            f"{name} must be a positive finite number."
        )
    return value


def positive_integer_from_env(name: str, default: str) -> int:
    raw_value = os.getenv(name, default).strip()
    if not re.fullmatch(r"[1-9][0-9]*", raw_value):
        raise ImproperlyConfigured(f"{name} must be a positive integer.")
    return int(raw_value)


def non_negative_integer_from_env(name: str, default: str) -> int:
    raw_value = os.getenv(name, default).strip()
    if not re.fullmatch(r"0|[1-9][0-9]*", raw_value):
        raise ImproperlyConfigured(f"{name} must be a non-negative integer.")
    return int(raw_value)


def strict_boolean_from_env(name: str, default: str) -> bool:
    raw_value = os.getenv(name, default).strip().lower()
    if raw_value not in {"true", "false"}:
        raise ImproperlyConfigured(f"{name} must be True or False.")
    return raw_value == "true"


def email_port_from_env(name: str = "EMAIL_PORT", default: str = "587") -> int:
    raw_value = os.getenv(name, default).strip()
    if not re.fullmatch(r"[1-9][0-9]*", raw_value):
        raise ImproperlyConfigured(f"{name} must be an integer from 1 to 65535.")
    value = int(raw_value)
    if value > 65535:
        raise ImproperlyConfigured(f"{name} must be an integer from 1 to 65535.")
    return value


PUBLIC_HOST_LABEL_PATTERN = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$",
    re.IGNORECASE,
)
NUMERIC_IPV4_HOST_PATTERN = re.compile(
    r"^(?:0x[0-9a-f]+|\d+)(?:\.(?:0x[0-9a-f]+|\d+))*$",
    re.IGNORECASE,
)


def is_public_hostname(hostname: str) -> bool:
    """Accept public DNS names or globally routable literal IP addresses."""
    candidate = hostname.strip().lower().rstrip(".")
    if candidate.startswith("."):
        return False
    if (
        not candidate
        or candidate == "localhost"
        or candidate.endswith(".localhost")
        or candidate.endswith(".local")
        or candidate.endswith(".internal")
    ):
        return False
    try:
        return ip_address(candidate.strip("[]")).is_global
    except ValueError:
        # Browsers accept shortened, octal and hexadecimal IPv4 spellings and
        # normalize them to a private/loopback address (for example 127.1).
        # Never reinterpret an all-numeric host as a DNS name.
        if NUMERIC_IPV4_HOST_PATTERN.fullmatch(candidate):
            return False
        labels = candidate.split(".")
        return (
            len(candidate) <= 253
            and len(labels) >= 2
            and all(PUBLIC_HOST_LABEL_PATTERN.fullmatch(label) for label in labels)
        )


def validate_public_https_url(
    value: str,
    *,
    setting_name: str,
    origin_only: bool = False,
) -> None:
    """Fail closed for public URLs that could leak credentials or target an intranet."""
    try:
        parsed = urlparse(value)
        parsed_port = parsed.port
    except ValueError as exc:
        raise ImproperlyConfigured(
            f"{setting_name} must be a credential-free public HTTPS URL."
        ) from exc
    invalid = (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or "@" in parsed.netloc
        or any(character.isspace() for character in parsed.netloc)
        or bool(parsed.query)
        or bool(parsed.fragment)
        or (origin_only and bool(parsed.path))
        or not is_public_hostname(parsed.hostname or "")
    )
    # Reading parsed.port above validates malformed and out-of-range ports.
    _ = parsed_port
    if invalid:
        raise ImproperlyConfigured(
            f"{setting_name} must be a credential-free public HTTPS URL."
        )


def validate_production_email(value: str, *, setting_name: str) -> None:
    try:
        validate_email(value)
    except ValidationError as exc:
        raise ImproperlyConfigured(
            f"{setting_name} must be a valid email address at a public domain."
        ) from exc
    domain = value.rsplit("@", 1)[-1]
    if not is_public_hostname(domain):
        raise ImproperlyConfigured(
            f"{setting_name} must be a valid email address at a public domain."
        )


# Fail closed: an unset secret must never silently fall back to a weak default.
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "").strip()
if not SECRET_KEY:
    raise ImproperlyConfigured(
        "DJANGO_SECRET_KEY is required. Set it in the environment or the project .env file."
    )
# Default to the safe side; development must opt in to DEBUG explicitly.
DEBUG = os.getenv("DJANGO_DEBUG", "False").lower() == "true"
DJANGO_ENV = os.getenv("DJANGO_ENV", "development").strip().lower()
if DJANGO_ENV not in {"development", "test", "production"}:
    raise ImproperlyConfigured(
        "DJANGO_ENV must be development, test or production."
    )
BEHIND_PROXY = os.getenv("DJANGO_BEHIND_PROXY", "False").lower() == "true"
raw_num_proxies = os.getenv(
    "DJANGO_NUM_PROXIES",
    "1" if BEHIND_PROXY else "0",
).strip()
try:
    if not re.fullmatch(r"[0-9]+", raw_num_proxies):
        raise ValueError
    DJANGO_NUM_PROXIES = int(raw_num_proxies)
except ValueError as exc:
    raise ImproperlyConfigured(
        "DJANGO_NUM_PROXIES must be a non-negative integer."
    ) from exc
if BEHIND_PROXY and DJANGO_NUM_PROXIES == 0:
    raise ImproperlyConfigured(
        "DJANGO_NUM_PROXIES must be greater than 0 when "
        "DJANGO_BEHIND_PROXY=True."
    )
if not BEHIND_PROXY and DJANGO_NUM_PROXIES != 0:
    raise ImproperlyConfigured(
        "DJANGO_NUM_PROXIES must be 0 when DJANGO_BEHIND_PROXY=False."
    )
if DJANGO_ENV == "production":
    insecure_secret = (
        SECRET_KEY.startswith("development-only")
        or SECRET_KEY.startswith("replace-this")
        or len(SECRET_KEY) < 50
        or len(set(SECRET_KEY)) < 10
    )
    if insecure_secret:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY must be at least 50 characters and unique in production.")
    if DEBUG:
        raise ImproperlyConfigured("DJANGO_DEBUG must be False in production.")
ALLOWED_HOSTS = [item.strip() for item in os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if item.strip()]
if DJANGO_ENV == "production":
    unsafe_allowed_hosts = [
        item
        for item in ALLOWED_HOSTS
        if (
            item == "*"
            or any(character in item for character in "/@?#")
            or "://" in item
            or any(character.isspace() for character in item)
            or not is_public_hostname(item)
        )
    ]
    if not ALLOWED_HOSTS or unsafe_allowed_hosts:
        raise ImproperlyConfigured(
            "DJANGO_ALLOWED_HOSTS must contain only explicit public hostnames "
            "or globally routable IP addresses in production."
        )
if DEBUG and "testserver" not in ALLOWED_HOSTS:
    # Enables Django's in-process API test client without weakening production hosts.
    ALLOWED_HOSTS.append("testserver")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "rest_framework",
    "catalog",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "catalog.upload_limits.RequestBodyLimitMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "catalog.middleware.AdminLoginRateLimitMiddleware",
    "catalog.middleware.PublicFormOriginMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
        "catalog.context_processors.admin_dashboard",
    ]},
}]
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"


def database_from_url(url: str) -> dict[str, object]:
    parsed = urlparse(url)
    if parsed.scheme.startswith("sqlite"):
        # Three URL slashes denote a relative SQLite path; four retain the
        # leading slash of a POSIX absolute path (sqlite:////tmp/example.db).
        return {"ENGINE": "django.db.backends.sqlite3", "NAME": parsed.path.removeprefix("/") or BASE_DIR / "db.sqlite3"}
    options = dict(parse_qsl(parsed.query, keep_blank_values=True))
    options.setdefault("connect_timeout", "5")
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": unquote(parsed.path.lstrip("/")),
        "USER": unquote(parsed.username or ""),
        "PASSWORD": unquote(parsed.password or ""),
        "HOST": parsed.hostname or "localhost",
        "PORT": str(parsed.port or 5432),
        "OPTIONS": options,
    }


explicit_database_url = os.getenv("DATABASE_URL", "").strip()
DATABASES = {
    "default": (
        database_from_url(explicit_database_url)
        if explicit_database_url
        else postgres_database_from_components(os.environ)
        or database_from_url("postgresql://trailer:change-me-locally@localhost:5432/trailer_parts")
    )
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = os.getenv("DJANGO_PUBLIC_MEDIA_URL", "").strip() or "/media/"
if not MEDIA_URL.endswith("/"):
    MEDIA_URL = f"{MEDIA_URL}/"
if DJANGO_ENV == "production":
    validate_public_https_url(
        MEDIA_URL,
        setting_name="DJANGO_PUBLIC_MEDIA_URL",
    )
MEDIA_ROOT = BASE_DIR / "media"
PRIVATE_MEDIA_ROOT = BASE_DIR / "private_media"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
FILE_UPLOAD_MAX_MEMORY_SIZE = 1 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 12 * 1024 * 1024
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_TOTAL_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_UPLOAD_FILES = 5
# Bound all request buffering, including ASGI bodies and Admin formsets:
# 25 MiB files + the existing 12 MiB form-data budget + 1 MiB multipart overhead.
MAX_REQUEST_BODY_BYTES = MAX_TOTAL_UPLOAD_BYTES + DATA_UPLOAD_MAX_MEMORY_SIZE + 1024 * 1024
FILE_UPLOAD_HANDLERS = [
    "catalog.upload_limits.RFQUploadLimitHandler",
    "django.core.files.uploadhandler.MemoryFileUploadHandler",
    "django.core.files.uploadhandler.TemporaryFileUploadHandler",
]
ALLOWED_UPLOAD_EXTENSIONS = {"pdf", "dwg", "dxf", "step", "stp", "png", "jpg", "jpeg", "webp"}
ATTACHMENT_SCANNER_COMMAND = os.getenv("ATTACHMENT_SCANNER_COMMAND", "").strip()
ATTACHMENT_SCAN_TIMEOUT = positive_integer_from_env("ATTACHMENT_SCAN_TIMEOUT", "60")
ATTACHMENT_ALLOW_UNSCANNED_IN_DEVELOPMENT = (
    DJANGO_ENV != "production"
    and os.getenv("ATTACHMENT_ALLOW_UNSCANNED_IN_DEVELOPMENT", "True").lower() == "true"
)
TURNSTILE_SECRET_KEY = os.getenv("TURNSTILE_SECRET_KEY", "").strip()
TURNSTILE_REQUIRED = (
    DJANGO_ENV == "production"
    or os.getenv("TURNSTILE_REQUIRED", "False").lower() == "true"
)
TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
TURNSTILE_TIMEOUT = positive_integer_from_env("TURNSTILE_TIMEOUT", "8")
if DJANGO_ENV == "production" and not TURNSTILE_SECRET_KEY:
    raise ImproperlyConfigured("TURNSTILE_SECRET_KEY is required in production.")
if DJANGO_ENV == "production" and TURNSTILE_SECRET_KEY in {
    "1x0000000000000000000000000000000AA",
    "2x0000000000000000000000000000000AA",
    "3x0000000000000000000000000000000AA",
}:
    raise ImproperlyConfigured("TURNSTILE_SECRET_KEY must not use a public testing key in production.")

ADMIN_LOGIN_MAX_FAILURES = positive_integer_from_env("ADMIN_LOGIN_MAX_FAILURES", "5")
ADMIN_LOGIN_FAILURE_WINDOW = positive_integer_from_env("ADMIN_LOGIN_FAILURE_WINDOW", "900")

GEOIP_COUNTRY_HEADER = os.getenv("DJANGO_GEOIP_COUNTRY_HEADER", "").strip()
GEOIP_CITY_HEADER = os.getenv("DJANGO_GEOIP_CITY_HEADER", "").strip()
if (GEOIP_COUNTRY_HEADER or GEOIP_CITY_HEADER) and not BEHIND_PROXY:
    raise ImproperlyConfigured(
        "Trusted GeoIP headers require DJANGO_BEHIND_PROXY=True."
    )

SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
SESSION_COOKIE_SECURE = DJANGO_ENV == "production"
CSRF_COOKIE_SECURE = DJANGO_ENV == "production"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SECURE_SSL_REDIRECT = os.getenv("DJANGO_SECURE_SSL_REDIRECT", "False").lower() == "true"
SECURE_HSTS_SECONDS = non_negative_integer_from_env(
    "DJANGO_SECURE_HSTS_SECONDS",
    "31536000" if DJANGO_ENV == "production" else "0",
)
SECURE_HSTS_INCLUDE_SUBDOMAINS = os.getenv("DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS", "False").lower() == "true"
SECURE_HSTS_PRELOAD = os.getenv("DJANGO_SECURE_HSTS_PRELOAD", "False").lower() == "true"
if BEHIND_PROXY:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
if DJANGO_ENV == "production" and not (SECURE_SSL_REDIRECT or BEHIND_PROXY):
    raise ImproperlyConfigured("Enable DJANGO_SECURE_SSL_REDIRECT or configure DJANGO_BEHIND_PROXY in production.")

default_cors_origins = "http://localhost:3000,http://127.0.0.1:3000,http://localhost:3001,http://127.0.0.1:3001" if DEBUG else ""
CORS_ALLOWED_ORIGINS = [item.strip() for item in os.getenv("DJANGO_CORS_ORIGINS", default_cors_origins).split(",") if item.strip()]
if DJANGO_ENV == "production" and not CORS_ALLOWED_ORIGINS:
    raise ImproperlyConfigured("DJANGO_CORS_ORIGINS must list the approved frontend origins in production.")
if DJANGO_ENV == "production":
    for cors_origin in CORS_ALLOWED_ORIGINS:
        validate_public_https_url(
            cors_origin,
            setting_name="DJANGO_CORS_ORIGINS",
            origin_only=True,
        )
CSRF_TRUSTED_ORIGINS = CORS_ALLOWED_ORIGINS
CORS_ALLOW_HEADERS = (*default_headers, "idempotency-key")

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["catalog.renderers.VersionedJSONRenderer"],
    # Preserve the public RFQ many=True error contract on DRF 3.18.
    # This compatibility option is scheduled for removal in DRF 3.20.
    "LIST_SERIALIZER_ERRORS_AS_DICT": False,
    "URL_FORMAT_OVERRIDE": None,
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 12,
    "NUM_PROXIES": DJANGO_NUM_PROXIES,
    "DEFAULT_THROTTLE_RATES": {
        "rfq_ip": "5/hour",
        "rfq_email": "5/hour",
        "rfq_upload_ip": "3/hour",
        "contact_ip": "10/hour",
        "contact_email": "10/hour",
        "analytics": "120/hour",
        "attribution": "600/hour",
        "document_download": "60/hour",
    },
}

REDIS_URL = (
    os.getenv("REDIS_URL", "").strip()
    or redis_url_from_components(os.environ, 0)
    or "redis://127.0.0.1:6379/0"
)
CACHE_URL = (
    os.getenv("DJANGO_CACHE_URL", "").strip()
    or redis_url_from_components(os.environ, 1)
)
if DJANGO_ENV == "test":
    # Tests must remain deterministic even when a developer's root .env points
    # at Redis or the service is intentionally offline.
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "trailer-parts-test",
        }
    }
elif DEBUG and not CACHE_URL:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "trailer-parts-dev"}}
else:
    CACHES = {
        "default": {
            "BACKEND": "config.cache.AtomicRedisCache",
            "LOCATION": CACHE_URL or REDIS_URL,
            "KEY_PREFIX": os.getenv("DJANGO_CACHE_KEY_PREFIX", "trailer-parts"),
        }
    }
CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
CELERY_REDIS_SOCKET_CONNECT_TIMEOUT = positive_float_from_env(
    "CELERY_REDIS_SOCKET_CONNECT_TIMEOUT",
    "1",
)
CELERY_REDIS_SOCKET_TIMEOUT = positive_float_from_env(
    "CELERY_REDIS_SOCKET_TIMEOUT",
    "2",
)
CELERY_TASK_PUBLISH_MAX_RETRIES = positive_integer_from_env(
    "CELERY_TASK_PUBLISH_MAX_RETRIES",
    "2",
)
CELERY_TASK_PUBLISH_RETRY_INTERVAL = positive_float_from_env(
    "CELERY_TASK_PUBLISH_RETRY_INTERVAL",
    "0.25",
)
CELERY_REDIS_RETRY_ON_TIMEOUT = False
CELERY_BROKER_CONNECTION_TIMEOUT = CELERY_REDIS_SOCKET_CONNECT_TIMEOUT
CELERY_BROKER_TRANSPORT_OPTIONS = {
    "socket_connect_timeout": CELERY_REDIS_SOCKET_CONNECT_TIMEOUT,
    "socket_timeout": CELERY_REDIS_SOCKET_TIMEOUT,
    "retry_on_timeout": False,
}
CELERY_RESULT_BACKEND_ALWAYS_RETRY = False
CELERY_RESULT_BACKEND_TRANSPORT_OPTIONS = {
    "retry_policy": {
        "max_retries": CELERY_TASK_PUBLISH_MAX_RETRIES,
        "interval_start": 0,
        "interval_step": CELERY_TASK_PUBLISH_RETRY_INTERVAL,
        "interval_max": CELERY_TASK_PUBLISH_RETRY_INTERVAL,
    },
}
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 300
NOTIFICATION_CLAIM_LEASE_SECONDS = positive_integer_from_env(
    "NOTIFICATION_CLAIM_LEASE_SECONDS", "600"
)
if NOTIFICATION_CLAIM_LEASE_SECONDS <= CELERY_TASK_TIME_LIMIT:
    raise ImproperlyConfigured(
        "NOTIFICATION_CLAIM_LEASE_SECONDS must exceed CELERY_TASK_TIME_LIMIT."
    )
CELERY_TASK_PUBLISH_RETRY = True
CELERY_TASK_PUBLISH_RETRY_POLICY = {
    "max_retries": CELERY_TASK_PUBLISH_MAX_RETRIES,
    "interval_start": 0,
    "interval_step": CELERY_TASK_PUBLISH_RETRY_INTERVAL,
    "interval_max": CELERY_TASK_PUBLISH_RETRY_INTERVAL,
}

DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "website@localhost").strip()
SALES_NOTIFICATION_EMAIL = os.getenv(
    "SALES_NOTIFICATION_EMAIL",
    "sales@localhost",
).strip()
DJANGO_PUBLIC_BASE_URL = os.getenv("DJANGO_PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
if DJANGO_ENV == "production":
    validate_public_https_url(
        DJANGO_PUBLIC_BASE_URL,
        setting_name="DJANGO_PUBLIC_BASE_URL",
    )
EMAIL_BACKEND = os.getenv(
    "EMAIL_BACKEND",
    "django.core.mail.backends.smtp.EmailBackend"
    if DJANGO_ENV == "production"
    else "django.core.mail.backends.console.EmailBackend",
).strip()
EMAIL_HOST = os.getenv("EMAIL_HOST", "").strip()
EMAIL_PORT = email_port_from_env()
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = strict_boolean_from_env("EMAIL_USE_TLS", "True")
EMAIL_USE_SSL = strict_boolean_from_env("EMAIL_USE_SSL", "False")
EMAIL_TIMEOUT = positive_float_from_env("EMAIL_TIMEOUT", "15")
if EMAIL_USE_TLS and EMAIL_USE_SSL:
    raise ImproperlyConfigured(
        "EMAIL_USE_TLS and EMAIL_USE_SSL cannot both be True."
    )
if DJANGO_ENV == "production":
    validate_production_email(
        DEFAULT_FROM_EMAIL,
        setting_name="DEFAULT_FROM_EMAIL",
    )
    validate_production_email(
        SALES_NOTIFICATION_EMAIL,
        setting_name="SALES_NOTIFICATION_EMAIL",
    )
    if EMAIL_BACKEND != "django.core.mail.backends.smtp.EmailBackend":
        raise ImproperlyConfigured(
            "EMAIL_BACKEND must be django.core.mail.backends.smtp.EmailBackend "
            "in production."
        )
    if not EMAIL_HOST:
        raise ImproperlyConfigured("EMAIL_HOST must be configured for SMTP delivery in production.")
    if not (EMAIL_USE_TLS or EMAIL_USE_SSL):
        raise ImproperlyConfigured(
            "Enable EMAIL_USE_TLS or EMAIL_USE_SSL for encrypted SMTP delivery "
            "in production."
        )
