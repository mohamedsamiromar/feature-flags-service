"""Query layer for core infrastructure probes (DB + cache reachability)."""

import uuid

from django.core.cache import cache
from django.db import connection


class HealthQuery:
    @staticmethod
    def ping_database() -> None:
        """Cheap SELECT 1 — confirms the connection pool + PostgreSQL are reachable.
        Raises the underlying DB exception on failure."""
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")

    @staticmethod
    def ping_cache() -> None:
        """Write a unique sentinel and read it back — confirms Redis read+write.
        The unique value prevents a stale cached result from masking a failure."""
        sentinel_key = f"healthz:{uuid.uuid4().hex}"
        cache.set(sentinel_key, "1", timeout=5)
        if cache.get(sentinel_key) != "1":
            raise RuntimeError("sentinel value mismatch")
        cache.delete(sentinel_key)
