"""Persisted alert history, so you are pinged once per find - not every run."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from .model import Offer

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1


class State:
    """Remembers the best price already announced for each sku+condition."""

    def __init__(self, path: Path, repeat_after_hours: float = 24.0) -> None:
        self.path = Path(path)
        self.repeat_after_seconds = max(repeat_after_hours, 0) * 3600
        self.seen: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("could not read state at %s (%s) - starting fresh", self.path, exc)
            return
        if data.get("version") != SCHEMA_VERSION:
            log.warning("state schema %s is not %s - starting fresh",
                        data.get("version"), SCHEMA_VERSION)
            return
        self.seen = data.get("seen") or {}

    def save(self) -> None:
        """Write atomically so a killed container cannot leave half a file."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": SCHEMA_VERSION,
            "updated_at": time.time(),
            "seen": self.seen,
        }
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def is_new(self, offer: Offer, now: float | None = None) -> tuple[bool, str]:
        """Should this offer be announced? Returns (yes, why)."""
        now = time.time() if now is None else now
        prior = self.seen.get(offer.key)
        if prior is None:
            return True, "new listing"

        prior_price = prior.get("price")
        if isinstance(prior_price, (int, float)) and offer.price < prior_price - 0.001:
            return True, f"price dropped from ${prior_price:,.2f}"

        last = prior.get("alerted_at", 0)
        if self.repeat_after_seconds and now - last >= self.repeat_after_seconds:
            return True, "still available (reminder)"
        return False, "already alerted"

    def record(self, offer: Offer, alerted: bool, now: float | None = None) -> None:
        now = time.time() if now is None else now
        prior = self.seen.get(offer.key, {})
        self.seen[offer.key] = {
            "sku": offer.sku,
            "name": offer.name,
            "condition": offer.condition,
            "price": min(offer.price, prior.get("price", offer.price)),
            "last_price": offer.price,
            "last_seen_at": now,
            "alerted_at": now if alerted else prior.get("alerted_at", 0),
        }

    def prune(self, live_keys: set[str], max_age_days: int = 90) -> None:
        """Drop entries not seen recently, so state cannot grow forever."""
        cutoff = time.time() - max_age_days * 86400
        self.seen = {
            key: value
            for key, value in self.seen.items()
            if key in live_keys or value.get("last_seen_at", 0) >= cutoff
        }
