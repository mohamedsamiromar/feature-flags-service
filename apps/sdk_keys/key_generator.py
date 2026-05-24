"""
Responsible for one thing: generating and hashing SDK keys.
No Django, no models, no HTTP — pure domain logic.
"""

import hashlib
import secrets


class KeyGenerator:
    SERVER_PREFIX = "sdk_srv_"
    CLIENT_PREFIX = "sdk_cli_"

    @classmethod
    def generate(cls, key_type: str) -> tuple[str, str, str]:
        """
        Build a cryptographically random SDK key.
        Returns (full_key, prefix, hashed_key).

        full_key  — shown to the caller exactly once; must never be stored.
        prefix    — first 16 chars, stored for display/identification.
        hashed_key — SHA-256 hex, stored for lookup during authentication.
        """
        type_prefix = cls.SERVER_PREFIX if key_type == "server" else cls.CLIENT_PREFIX
        token = secrets.token_urlsafe(32)
        full_key = f"{type_prefix}{token}"
        prefix = full_key[:16]
        hashed = hashlib.sha256(full_key.encode()).hexdigest()
        return full_key, prefix, hashed

    @staticmethod
    def hash_raw(raw_key: str) -> str:
        """Deterministic SHA-256 hex digest of a raw key string."""
        return hashlib.sha256(raw_key.encode()).hexdigest()
