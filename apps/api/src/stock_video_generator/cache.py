from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any


class MarketDataCache:
    """File cache that keeps request provenance next to normalized provider data."""

    def __init__(
        self,
        root: Path,
        *,
        recent_ttl_seconds: int,
        historical_ttl_seconds: int,
    ) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.recent_ttl_seconds = recent_ttl_seconds
        self.historical_ttl_seconds = historical_ttl_seconds

    @staticmethod
    def make_key(provider: str, operation: str, parameters: dict[str, object]) -> str:
        canonical = json.dumps(
            {
                "provider": provider,
                "operation": operation,
                "parameters": parameters,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _path(self, key: str) -> Path:
        return self.root / f"{key}.json"

    def get(self, key: str) -> dict[str, Any] | None:
        path = self._path(key)
        if not path.exists():
            return None
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
            expires_at = datetime.fromisoformat(envelope["expires_at"])
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            return None
        if expires_at <= datetime.now(UTC):
            return None
        envelope["cache_hit"] = True
        return envelope

    def put(
        self,
        key: str,
        *,
        provider: str,
        operation: str,
        parameters: dict[str, object],
        payload: object,
        raw_response_summary: dict[str, object],
        validation: dict[str, object] | None = None,
        requested_end: date | None = None,
    ) -> dict[str, Any]:
        now = datetime.now(UTC)
        is_recent = requested_end is None or requested_end >= date.today() - timedelta(days=7)
        ttl = self.recent_ttl_seconds if is_recent else self.historical_ttl_seconds
        envelope: dict[str, Any] = {
            "schema_version": "1.0",
            "cache_key": key,
            "provider": provider,
            "operation": operation,
            "parameters": parameters,
            "fetched_at": now.isoformat(),
            "expires_at": (now + timedelta(seconds=ttl)).isoformat(),
            "cache_hit": False,
            "raw_response_summary": raw_response_summary,
            "validation": validation,
            "payload": payload,
        }
        temporary_path = self._path(key).with_suffix(".tmp")
        temporary_path.write_text(
            json.dumps(envelope, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary_path.replace(self._path(key))
        return envelope
