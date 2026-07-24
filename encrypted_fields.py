"""
Transparent AES-128 field-level encryption (Fernet = AES-128-CBC + HMAC-SHA256).

Generate a key once and add it to your .env:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

.env:
    FIELD_ENCRYPTION_KEY=<paste-key-here>

Encrypted fields are stored as TEXT columns in the database.
Filtering/ordering on encrypted fields is not supported — use FK or non-sensitive
fields (status, created_at, etc.) for queries instead.
"""
import logging
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from django.db import models

logger = logging.getLogger(__name__)

# Ephemeral fallback key used only when FIELD_ENCRYPTION_KEY is not configured.
# Data encrypted with this key is lost on process restart — development only.
_ephemeral_key: Optional[str] = None


def _fernet() -> Fernet:
    from django.conf import settings

    key = getattr(settings, 'FIELD_ENCRYPTION_KEY', None)
    if key:
        return Fernet(key.encode() if isinstance(key, str) else key)

    global _ephemeral_key
    if _ephemeral_key is None:
        _ephemeral_key = Fernet.generate_key().decode()
        logger.warning(
            'FIELD_ENCRYPTION_KEY is not set. An ephemeral key has been generated — '
            'encrypted data will be unreadable after process restart. '
            'Set FIELD_ENCRYPTION_KEY in your .env file.'
        )
    return Fernet(_ephemeral_key.encode())


def encrypt_value(plaintext: str) -> str:
    """Return Fernet-encrypted, base64-encoded ciphertext."""
    if plaintext is None or plaintext == '':
        return plaintext
    return _fernet().encrypt(str(plaintext).encode()).decode()


def decrypt_value(ciphertext: str) -> Optional[str]:
    """
    Return decrypted plaintext.
    If decryption fails (plaintext legacy data before migration), returns value as-is.
    """
    if ciphertext is None or ciphertext == '':
        return ciphertext
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except (InvalidToken, Exception):
        return ciphertext  # not yet encrypted — safe fallback for existing rows


class EncryptedFieldMixin:
    """
    Mixin that stores data as encrypted TEXT in the DB while exposing plaintext in Python.
    Decrypts on load (from_db_value), encrypts on save (get_prep_value).
    """

    def get_internal_type(self):
        # Force TEXT column so the longer ciphertext fits regardless of max_length.
        return 'TextField'

    def from_db_value(self, value, expression, connection):
        return decrypt_value(value)

    def get_prep_value(self, value):
        # Skip parent's get_prep_value to avoid max_length enforcement on ciphertext.
        if value is None or value == '':
            return value
        return encrypt_value(str(value))


class EncryptedCharField(EncryptedFieldMixin, models.CharField):
    """CharField with transparent at-rest encryption. max_length validates plaintext."""


class EncryptedTextField(EncryptedFieldMixin, models.TextField):
    """TextField with transparent at-rest encryption."""


class EncryptedJSONField(EncryptedFieldMixin, models.TextField):
    """
    Stores a Python list/dict as an encrypted JSON string in the DB.
    Returned as a Python object on load (list/dict).
    """

    def from_db_value(self, value, expression, connection):
        import json
        raw = decrypt_value(value)
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
            # Always return a list or dict — never a bare string/int
            return parsed if isinstance(parsed, (list, dict)) else []
        except (TypeError, ValueError):
            return []

    def get_prep_value(self, value):
        import json
        if value is None:
            return value
        blob = json.dumps(value) if not isinstance(value, str) else value
        return encrypt_value(blob)

    def to_python(self, value):
        import json
        if isinstance(value, (list, dict)):
            return value
        if not value:
            return []
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return value
