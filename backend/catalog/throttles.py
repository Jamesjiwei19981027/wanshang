from __future__ import annotations

import hashlib

from .rate_limits import AtomicSlidingWindowThrottle


class EmailRateThrottle(AtomicSlidingWindowThrottle):
    """Count only an explicitly supplied, serializer-validated email address.

    This throttle runs after form and CAPTCHA validation, rather than as an
    initial request throttle. Never derive a global email quota from raw data.
    """

    # A new namespace prevents old, unverified quota entries from continuing
    # to lock out customers after deployment; other throttle caches stay intact.
    cache_format = "throttle_validated_%(scope)s_%(ident)s"

    def __init__(self, *, email: str):
        self.validated_email = email
        super().__init__()

    def get_cache_key(self, request, view) -> str | None:
        email = self.validated_email.strip().lower()
        if not email:
            return None
        try:
            encoded_email = email.encode("utf-8")
        except UnicodeEncodeError:
            # The serializer rejects surrogates; keep hashing defensive too.
            return None
        email_hash = hashlib.sha256(encoded_email).hexdigest()
        return self.cache_format % {"scope": self.scope, "ident": email_hash}


class IpRateThrottle(AtomicSlidingWindowThrottle):
    def get_cache_key(self, request, view) -> str:
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}


class RFQIpThrottle(IpRateThrottle):
    scope = "rfq_ip"


class RFQEmailThrottle(EmailRateThrottle):
    scope = "rfq_email"


class AttachmentUploadIpThrottle(IpRateThrottle):
    """Use an independent, stricter bucket only for RFQs carrying files."""

    scope = "rfq_upload_ip"

    def get_cache_key(self, request, view) -> str | None:
        if not request.FILES:
            return None
        return super().get_cache_key(request, view)


class ContactIpThrottle(IpRateThrottle):
    scope = "contact_ip"


class ContactEmailThrottle(EmailRateThrottle):
    scope = "contact_email"


class AnalyticsEventThrottle(IpRateThrottle):
    scope = "analytics"


class AttributionThrottle(IpRateThrottle):
    scope = "attribution"


class DocumentDownloadThrottle(IpRateThrottle):
    scope = "document_download"
