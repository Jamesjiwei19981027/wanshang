"""Fingerprint retryable public submissions without retaining request identifiers."""
import hashlib
import hmac
import json
from collections.abc import Mapping
from uuid import RFC_4122, UUID

from django.conf import settings
from rest_framework.exceptions import APIException, ValidationError


# A refreshed challenge and source context must not turn a retry into a new lead.
TRANSIENT_FIELDS = frozenset({
    "turnstile_token", "cf-turnstile-response", "session_id", "source_url",
    "referrer_url", "first_touch_source", "last_touch_source", "utm_source",
    "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "first_landing_page", "last_landing_page",
})


class SubmissionConflict(APIException):
    status_code = 409
    default_detail = "This submission identifier was already used for different content. Start a new submission."
    default_code = "submission_conflict"


def _digest(value: bytes) -> str:
    return hmac.new(settings.SECRET_KEY.encode("utf-8"), value, hashlib.sha256).hexdigest()


def submission_key_hash(request, kind: str) -> str | None:
    value = request.headers.get("Idempotency-Key")
    if value is None:
        return None
    try:
        if len(value) != 36:
            raise ValueError
        identifier = UUID(value)
        if identifier.version != 4 or identifier.variant != RFC_4122:
            raise ValueError
    except (ValueError, AttributeError) as exc:
        raise ValidationError({"submission": "Idempotency-Key must be a version 4 UUID."}) from exc
    return _digest(f"public-submission:v1:{kind}:{identifier}".encode("ascii"))


def submission_payload_hash(request) -> str:
    data = request.data
    if not isinstance(data, Mapping):
        raise ValidationError({"submission": "Form submissions must be objects."})
    fields = {}
    for name in sorted(data):
        if name in TRANSIENT_FIELDS or name in request.FILES:
            continue
        fields[name] = data.getlist(name) if hasattr(data, "getlist") else [data[name]]
    files = []
    for name in sorted(request.FILES):
        for upload in request.FILES.getlist(name):
            position = upload.tell()
            checksum = hashlib.sha256()
            try:
                for chunk in upload.chunks():
                    checksum.update(chunk)
            finally:
                upload.seek(position)
            files.append([name, upload.name, upload.size, upload.content_type, checksum.hexdigest()])
    try:
        encoded = json.dumps({"fields": fields, "files": files}, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValidationError({"submission": "The submission contains invalid data."}) from exc
    return _digest(encoded.encode("utf-8"))
