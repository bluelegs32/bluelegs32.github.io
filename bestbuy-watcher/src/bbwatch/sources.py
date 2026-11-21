"""Fetching open-box offers from Best Buy.

Primary source is the official Best Buy Developer API (free key from
https://developer.bestbuy.com). Its `/beta/products/openBox` endpoint returns
the live open-box offers and grades for a SKU.

An optional secondary source reads the public JSON endpoint that
bestbuy.com's own product page calls. It is not a documented API, it can
change or disappear without notice, and it is off unless you opt in with
`allow_unofficial_source: true`.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Iterable, Sequence

import requests

from .model import Offer

log = logging.getLogger(__name__)

API_ROOT = "https://api.bestbuy.com"
OPEN_BOX_PATH = "/beta/products/openBox"
PRODUCTS_PATH = "/v1/products"
STORES_PATH = "/v1/stores"
PRICE_BLOCKS_URL = "https://www.bestbuy.com/api/3.0/priceBlocks"

USER_AGENT = "bbwatch/1.0 (personal open-box watcher)"
RETRY_STATUS = {429, 500, 502, 503, 504}


class SourceError(RuntimeError):
    """A source could not be queried."""


def _dig(obj: Any, *path: str, default: Any = None) -> Any:
    """Walk nested dicts safely; Best Buy nests the same field differently
    across endpoints, so callers try several paths."""
    cur = obj
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur if cur is not None else default


def _first(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", []):
            return value
    return None


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class BestBuyClient:
    """Thin HTTP client with retries and polite rate limiting.

    The Best Buy API allows 5 requests/second and 50k/day on a standard key;
    a small floor between calls keeps us well clear of that.
    """

    def __init__(
        self,
        api_key: str | None,
        *,
        session: requests.Session | None = None,
        timeout: int = 20,
        max_retries: int = 3,
        min_interval: float = 0.25,
    ) -> None:
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self.min_interval = min_interval
        self._last_call = 0.0
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})

    def _throttle(self) -> None:
        wait = self.min_interval - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def get_json(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            self._throttle()
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
            except requests.RequestException as exc:
                last_error = exc
            else:
                if resp.status_code == 403:
                    raise SourceError(
                        f"{url} returned 403 - check that BESTBUY_API_KEY is valid "
                        "and that the Open Box API is enabled for it"
                    )
                if resp.status_code in RETRY_STATUS:
                    last_error = SourceError(f"{url} returned HTTP {resp.status_code}")
                else:
                    resp.raise_for_status()
                    return resp.json()
            backoff = min(2 ** attempt, 30)
            log.warning("request failed (attempt %s/%s): %s", attempt, self.max_retries, last_error)
            if attempt < self.max_retries:
                time.sleep(backoff)
        raise SourceError(f"giving up on {url}: {last_error}")


def _parse_offers(result: dict[str, Any], source: str) -> list[Offer]:
    """Turn one openBox API result object into Offer records."""
    sku = str(_first(result.get("sku"), _dig(result, "product", "sku"), "")).strip()
    if not sku:
        return []

    name = str(
        _first(
            _dig(result, "names", "title"),
            result.get("name"),
            _dig(result, "names", "short"),
            f"SKU {sku}",
        )
    )
    regular = _as_float(
        _first(_dig(result, "prices", "regular"), result.get("regularPrice"))
    )
    product_url = _first(result.get("url"), _dig(result, "links", "web"))

    offers: list[Offer] = []
    for raw in result.get("offers") or []:
        condition = str(_first(raw.get("condition"), raw.get("conditionCode"), "unknown"))
        price = _as_float(
            _first(
                _dig(raw, "prices", "current"),
                _dig(raw, "prices", "currentPrice"),
                raw.get("currentPrice"),
                raw.get("price"),
            )
        )
        if price is None:
            log.debug("skipping offer with no price for sku %s (%s)", sku, condition)
            continue
        offers.append(
            Offer(
                sku=sku,
                name=name,
                condition=condition,
                price=price,
                regular_price=_as_float(
                    _first(_dig(raw, "prices", "regular"), regular)
                ),
                url=_first(_dig(raw, "links", "web"), product_url,
                           f"https://www.bestbuy.com/site/searchpage.jsp?st={sku}"),
                source=source,
                online_available=_first(
                    _dig(raw, "inventory", "onlineAvailability"),
                    raw.get("onlineAvailability"),
                ),
            )
        )
    return offers


def _chunk(items: Sequence[str], size: int) -> Iterable[Sequence[str]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def fetch_open_box(client: BestBuyClient, skus: Sequence[str]) -> list[Offer]:
    """Official API: open-box offers for the given SKUs."""
    if not skus:
        return []
    if not client.api_key:
        raise SourceError("no Best Buy API key configured")

    offers: list[Offer] = []
    for batch in _chunk(list(skus), 20):
        url = f"{API_ROOT}{OPEN_BOX_PATH}(sku in({','.join(batch)}))"
        payload = client.get_json(url, {"apiKey": client.api_key, "format": "json"})
        for result in payload.get("results") or []:
            offers.extend(_parse_offers(result, "bestbuy-api"))
    return offers


def search_skus(client: BestBuyClient, query: str, limit: int = 25) -> list[str]:
    """Official API: resolve a keyword search to SKUs worth checking."""
    if not client.api_key:
        raise SourceError("no Best Buy API key configured")
    terms = [t for t in query.replace('"', "").split() if t]
    if not terms:
        return []
    clause = "&".join(f"search={t}" for t in terms)
    url = f"{API_ROOT}{PRODUCTS_PATH}(({clause}))"
    payload = client.get_json(
        url,
        {
            "apiKey": client.api_key,
            "format": "json",
            "show": "sku,name,regularPrice",
            "pageSize": min(limit, 100),
            "sort": "regularPrice.desc",
        },
    )
    return [str(p["sku"]) for p in payload.get("products") or [] if p.get("sku")]


def fetch_store_availability(
    client: BestBuyClient, sku: str, zip_code: str, radius: int = 25, limit: int = 5
) -> list[str]:
    """Official API: nearby stores that list this SKU as in stock.

    Best Buy's store availability covers new stock, not open-box units
    specifically, so treat it as 'which stores near you carry this model'.
    """
    if not client.api_key:
        return []
    url = f'{API_ROOT}{STORES_PATH}(area("{zip_code}",{radius})+products.sku={sku})'
    try:
        payload = client.get_json(
            url,
            {
                "apiKey": client.api_key,
                "format": "json",
                "show": "name,city,distance",
                "pageSize": limit,
            },
        )
    except (SourceError, requests.RequestException) as exc:
        log.warning("store lookup failed for sku %s: %s", sku, exc)
        return []

    names = []
    for store in payload.get("stores") or []:
        label = store.get("name") or store.get("city") or "store"
        distance = store.get("distance")
        names.append(f"{label} ({distance} mi)" if distance is not None else str(label))
    return names


def fetch_open_box_unofficial(client: BestBuyClient, skus: Sequence[str]) -> list[Offer]:
    """Undocumented bestbuy.com endpoint, used only when explicitly enabled.

    This is the JSON the retail product page itself requests. It is not a
    supported API: the shape can change and requests may be blocked.
    """
    offers: list[Offer] = []
    for batch in _chunk(list(skus), 10):
        try:
            payload = client.get_json(PRICE_BLOCKS_URL, {"skus": ",".join(batch)})
        except (SourceError, requests.RequestException) as exc:
            log.warning("unofficial source failed: %s", exc)
            return offers

        blocks = payload if isinstance(payload, list) else payload.get("priceBlocks") or []
        for block in blocks:
            sku = str(_first(block.get("sku"), _dig(block, "sku", "skuId"), "")).strip()
            open_box = _dig(block, "sku", "openBox", default={}) or block.get("openBox") or {}
            result = {
                "sku": sku,
                "names": {"title": _first(_dig(block, "sku", "name"), f"SKU {sku}")},
                "prices": {"regular": _dig(block, "sku", "price", "regularPrice")},
                "url": _dig(block, "sku", "url"),
                "offers": open_box.get("offers") or [],
            }
            offers.extend(_parse_offers(result, "bestbuy-web"))
    return offers
