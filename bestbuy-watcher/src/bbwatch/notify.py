"""Push notifications: ntfy and Pushover."""

from __future__ import annotations

import logging
from typing import Sequence

import requests

from .config import Config, NtfyConfig, PushoverConfig
from .model import Offer

log = logging.getLogger(__name__)

TIMEOUT = 15


def format_title(offer: Offer) -> str:
    return f"${offer.price:,.0f} open-box {offer.condition.title()} - {offer.name[:70]}"


def format_body(offer: Offer, reason: str) -> str:
    lines = [
        offer.name,
        f"Condition: {offer.condition.title()}",
        f"Price: ${offer.price:,.2f}",
    ]
    if offer.regular_price:
        pct = offer.discount_pct
        saving = offer.regular_price - offer.price
        lines.append(
            f"Regular: ${offer.regular_price:,.2f} "
            f"(save ${saving:,.2f}{f', {pct}% off' if pct is not None else ''})"
        )
    if offer.online_available is not None:
        lines.append(f"Online: {'yes' if offer.online_available else 'no'}")
    if offer.stores:
        lines.append("Nearby: " + "; ".join(offer.stores))
    lines.append(f"SKU {offer.sku} - {reason}")
    return "\n".join(lines)


class Notifier:
    """Sends one alert to every channel that is turned on."""

    def __init__(self, config: Config, session: requests.Session | None = None) -> None:
        self.config = config
        self.session = session or requests.Session()

    def send(self, offer: Offer, reason: str) -> bool:
        """Returns True if at least one channel accepted the alert."""
        title = format_title(offer)
        body = format_body(offer, reason)
        delivered = False

        if self.config.ntfy.enabled:
            delivered |= self._send_ntfy(self.config.ntfy, title, body, offer)
        if self.config.pushover.enabled:
            delivered |= self._send_pushover(self.config.pushover, title, body, offer)

        if not delivered:
            log.error("no channel accepted the alert for sku %s", offer.sku)
        return delivered

    def _send_ntfy(self, cfg: NtfyConfig, title: str, body: str, offer: Offer) -> bool:
        url = f"{cfg.server.rstrip('/')}/{cfg.topic.lstrip('/')}"
        headers = {
            "Title": title,
            "Priority": cfg.priority,
            "Tags": "tv,moneybag",
        }
        if offer.url:
            headers["Click"] = offer.url
        auth = None
        if cfg.token:
            headers["Authorization"] = f"Bearer {cfg.token}"
        elif cfg.username and cfg.password:
            auth = (cfg.username, cfg.password)
        try:
            resp = self.session.post(
                url, data=body.encode("utf-8"), headers=headers, auth=auth, timeout=TIMEOUT
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            log.error("ntfy delivery failed: %s", exc)
            return False
        log.info("ntfy alert sent to %s", url)
        return True

    def _send_pushover(self, cfg: PushoverConfig, title: str, body: str, offer: Offer) -> bool:
        payload = {
            "token": cfg.token,
            "user": cfg.user,
            "title": title,
            "message": body,
            "priority": cfg.priority,
        }
        if cfg.device:
            payload["device"] = cfg.device
        if offer.url:
            payload["url"] = offer.url
            payload["url_title"] = "Open on Best Buy"
        if cfg.priority >= 2:
            payload.update({"retry": 60, "expire": 1800})
        try:
            resp = self.session.post(
                "https://api.pushover.net/1/messages.json", data=payload, timeout=TIMEOUT
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            log.error("pushover delivery failed: %s", exc)
            return False
        log.info("pushover alert sent")
        return True

    def send_heartbeat(self, text: str) -> None:
        """Low-priority 'still running' ping, used by --test."""
        if self.config.ntfy.enabled:
            cfg = self.config.ntfy
            url = f"{cfg.server.rstrip('/')}/{cfg.topic.lstrip('/')}"
            headers = {"Title": "bbwatch", "Priority": "low", "Tags": "white_check_mark"}
            if cfg.token:
                headers["Authorization"] = f"Bearer {cfg.token}"
            try:
                self.session.post(url, data=text.encode(), headers=headers, timeout=TIMEOUT)
            except requests.RequestException as exc:
                log.error("ntfy heartbeat failed: %s", exc)
        if self.config.pushover.enabled:
            cfg = self.config.pushover
            try:
                self.session.post(
                    "https://api.pushover.net/1/messages.json",
                    data={"token": cfg.token, "user": cfg.user,
                          "title": "bbwatch", "message": text, "priority": -1},
                    timeout=TIMEOUT,
                )
            except requests.RequestException as exc:
                log.error("pushover heartbeat failed: %s", exc)


def summarize(offers: Sequence[Offer]) -> str:
    if not offers:
        return "no matching open-box offers"
    return ", ".join(f"{o.sku} {o.condition} ${o.price:,.0f}" for o in offers)
