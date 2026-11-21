"""One pass of the hunt: fetch, match, de-duplicate, notify."""

from __future__ import annotations

import dataclasses
import logging

from .config import Config, Watch
from .model import Offer
from .notify import Notifier
from .sources import (
    BestBuyClient,
    SourceError,
    fetch_open_box,
    fetch_open_box_unofficial,
    fetch_store_availability,
    search_skus,
)
from .state import State

log = logging.getLogger(__name__)


@dataclasses.dataclass
class RunResult:
    checked_skus: int = 0
    offers_found: int = 0
    matches: list[tuple[Watch, Offer, str]] = dataclasses.field(default_factory=list)
    alerts_sent: int = 0
    errors: list[str] = dataclasses.field(default_factory=list)


def _skus_for_watch(client: BestBuyClient, watch: Watch) -> list[str]:
    skus = list(watch.skus)
    if watch.search:
        try:
            found = search_skus(client, watch.search)
        except (SourceError, Exception) as exc:  # noqa: BLE001 - search is best-effort
            log.warning("search %r failed for watch %r: %s", watch.search, watch.name, exc)
        else:
            log.info("search %r resolved to %s sku(s)", watch.search, len(found))
            skus.extend(s for s in found if s not in skus)
    return skus


def _collect_offers(client: BestBuyClient, config: Config, skus: list[str]) -> list[Offer]:
    offers: list[Offer] = []
    if client.api_key:
        offers.extend(fetch_open_box(client, skus))
    elif not config.allow_unofficial_source:
        raise SourceError(
            "no BESTBUY_API_KEY set and allow_unofficial_source is false - "
            "get a free key at https://developer.bestbuy.com"
        )

    if config.allow_unofficial_source:
        known = {o.key for o in offers}
        offers.extend(o for o in fetch_open_box_unofficial(client, skus) if o.key not in known)
    return offers


def run_once(config: Config, client: BestBuyClient, state: State, notifier: Notifier) -> RunResult:
    """Check every watch once and alert on anything new."""
    result = RunResult()
    live_keys: set[str] = set()

    for watch in config.watches:
        skus = _skus_for_watch(client, watch)
        if not skus:
            log.warning("watch %r resolved to no SKUs - skipping", watch.name)
            continue
        result.checked_skus += len(skus)

        try:
            offers = _collect_offers(client, config, skus)
        except SourceError as exc:
            log.error("watch %r: %s", watch.name, exc)
            result.errors.append(f"{watch.name}: {exc}")
            continue

        result.offers_found += len(offers)
        for offer in offers:
            live_keys.add(offer.key)
            matched, why_not = watch.matches(offer)
            if not matched:
                log.debug("watch %r skipped sku %s: %s", watch.name, offer.sku, why_not)
                state.record(offer, alerted=False)
                continue

            should_alert, reason = state.is_new(offer)
            if not should_alert:
                log.info("watch %r: sku %s %s - %s", watch.name, offer.sku, offer.condition, reason)
                state.record(offer, alerted=False)
                continue

            if config.check_store_availability and config.zip_code:
                stores = fetch_store_availability(
                    client, offer.sku, config.zip_code, config.store_radius_miles
                )
                offer = dataclasses.replace(offer, stores=tuple(stores), store_count=len(stores))

            log.info(
                "MATCH %s: %s %s $%.2f (%s)",
                watch.name, offer.sku, offer.condition, offer.price, reason,
            )
            result.matches.append((watch, offer, reason))
            sent = notifier.send(offer, reason)
            result.alerts_sent += int(sent)
            state.record(offer, alerted=sent)

    state.prune(live_keys)
    state.save()
    return result
