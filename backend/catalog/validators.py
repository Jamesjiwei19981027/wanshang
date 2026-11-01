from __future__ import annotations

from ipaddress import ip_address
import re
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator


_https_url_validator = URLValidator(schemes=("https",))
_public_host_label = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$",
    re.IGNORECASE,
)
_numeric_ipv4_host = re.compile(
    r"^(?:0x[0-9a-f]+|\d+)(?:\.(?:0x[0-9a-f]+|\d+))*$",
    re.IGNORECASE,
)


def is_public_hostname(hostname: str) -> bool:
    candidate = hostname.strip().lower().rstrip(".")
    if (
        not candidate
        or candidate.startswith(".")
        or candidate == "localhost"
        or candidate.endswith((".localhost", ".local", ".internal", ".test", ".invalid"))
    ):
        return False
    try:
        return ip_address(candidate.strip("[]")).is_global
    except ValueError:
        if _numeric_ipv4_host.fullmatch(candidate):
            return False
        labels = candidate.split(".")
        return (
            len(candidate) <= 253
            and len(labels) >= 2
            and all(_public_host_label.fullmatch(label) for label in labels)
        )


def validate_secure_public_url(value: str) -> None:
    """Allow only credential-free HTTPS URLs for public external content."""
    if not value:
        return

    _https_url_validator(value)
    try:
        parsed = urlsplit(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError("Enter a valid HTTPS URL.", code="invalid") from exc
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValidationError("Enter a valid HTTPS URL.", code="invalid")
    if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
        raise ValidationError(
            "URL credentials are not allowed.",
            code="credentials_not_allowed",
        )
    if not is_public_hostname(parsed.hostname):
        raise ValidationError(
            "URL host must be publicly routable.",
            code="non_public_host",
        )


def secure_public_url_or_blank(value: str) -> str:
    """Fail closed when serializing legacy or otherwise unvalidated public URLs."""
    if not value:
        return ""
    try:
        validate_secure_public_url(value)
    except (TypeError, ValueError, ValidationError):
        return ""
    return value


def validate_banner_link(value: str) -> None:
    """Allow site-relative paths or credential-free public HTTPS URLs only."""
    if not value:
        return
    candidate = value.strip()
    if not candidate:
        return
    if candidate.startswith("/"):
        if candidate.startswith("//") or "\\" in candidate or any(ch in candidate for ch in "\r\n\x00"):
            raise ValidationError(
                "Banner links must be a site path or a public HTTPS URL.",
                code="invalid_banner_link",
            )
        return
    validate_secure_public_url(candidate)


def sanitize_upload_filename(name: str) -> str:
    """Strip path separators and control characters from stored upload names."""
    cleaned = str(name or "").replace("\r", " ").replace("\n", " ").replace("\x00", "")
    cleaned = cleaned.replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = "".join(ch for ch in cleaned if ord(ch) >= 32)
    cleaned = " ".join(cleaned.split())
    return cleaned[:255]
