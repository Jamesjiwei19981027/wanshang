"""Encryption for AI provider API keys stored in the database.

The key is derived from DJANGO_SECRET_KEY, so a database backup alone does not
reveal provider keys. Changing the secret key makes saved keys unreadable;
staff then re-enter them in the admin.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def _fernet() -> Fernet:
    digest = hashlib.sha256(f"assistant-provider-api-key:{settings.SECRET_KEY}".encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(token: str) -> str | None:
    """Return the plain key, or None when it was saved under another secret key."""
    if not token:
        return None
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        return None
