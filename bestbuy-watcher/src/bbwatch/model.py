"""Core data types shared by sources, matching and notification."""

from __future__ import annotations

import dataclasses
from typing import Any

# Best Buy has used two naming schemes for open-box grades over the years.
# Rank them on one scale so `min_condition` means the same thing either way.
CONDITION_RANKS: dict[str, int] = {
    "fair": 1,
    "good": 2,
    "satisfactory": 2,
    "excellent": 3,
    "certified": 4,
    "certified - like new": 4,
    "open-box certified": 4,
}

UNKNOWN_CONDITION_RANK = 0


def condition_rank(condition: str | None) -> int:
    """Map a condition label to a comparable rank (higher is better)."""
    if not condition:
        return UNKNOWN_CONDITION_RANK
    return CONDITION_RANKS.get(condition.strip().lower(), UNKNOWN_CONDITION_RANK)


@dataclasses.dataclass(frozen=True)
class Offer:
    """A single open-box offer for one SKU at one condition grade."""

    sku: str
    name: str
    condition: str
    price: float
    regular_price: float | None = None
    url: str | None = None
    source: str = "bestbuy-api"
    online_available: bool | None = None
    store_count: int | None = None
    stores: tuple[str, ...] = ()

    @property
    def key(self) -> str:
        """Identity used for de-duplicating alerts across runs."""
        return f"{self.sku}:{self.condition.lower()}"

    @property
    def discount_pct(self) -> float | None:
        if not self.regular_price or self.regular_price <= 0:
            return None
        return round((1 - (self.price / self.regular_price)) * 100, 1)

    def to_dict(self) -> dict[str, Any]:
        data = dataclasses.asdict(self)
        data["stores"] = list(self.stores)
        data["discount_pct"] = self.discount_pct
        return data
