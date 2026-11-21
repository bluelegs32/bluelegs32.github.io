"""Command line entry point.

RUN_MODE=once   -> one pass, then exit (Fly scheduled machines, cron)
RUN_MODE=loop   -> pass every interval_seconds (Unraid, always-on Fly machine)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path

from .config import ConfigError, load_config
from .model import Offer
from .notify import Notifier
from .runner import run_once
from .sources import BestBuyClient
from .state import State

DEFAULT_CONFIG = os.environ.get("CONFIG_PATH", "/config/config.yml")
_stop = threading.Event()


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def _handle_signal(signum, _frame) -> None:
    logging.getLogger(__name__).info("got signal %s - shutting down after this pass", signum)
    _stop.set()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bbwatch", description="Best Buy open-box watcher")
    parser.add_argument("-c", "--config", default=DEFAULT_CONFIG, help="path to config.yml")
    parser.add_argument("--mode", choices=("once", "loop"),
                        default=os.environ.get("RUN_MODE", "loop"))
    parser.add_argument("--test-notify", action="store_true",
                        help="send a test notification and exit")
    parser.add_argument("--dry-run", action="store_true",
                        help="check and print matches without sending or saving state")
    parser.add_argument("--json", action="store_true", help="print results as JSON")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)
    log = logging.getLogger("bbwatch")

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        log.error("config error: %s", exc)
        return 2

    if not config.any_notifier_enabled:
        log.error("no notification channel is enabled - enable ntfy or pushover")
        return 2

    notifier = Notifier(config)

    if args.test_notify:
        notifier.send_heartbeat(
            f"Test alert. Watching {len(config.watches)} item(s); "
            f"state at {config.state_path}."
        )
        sample = Offer(
            sku="0000000", name="Test - LG 97\" G5 OLED", condition="excellent",
            price=2000.0, regular_price=24999.99,
            url="https://www.bestbuy.com/", source="test",
        )
        notifier.send(sample, "test notification")
        log.info("test notifications dispatched")
        return 0

    client = BestBuyClient(config.api_key)
    state = State(config.state_path, config.repeat_after_hours)

    if args.dry_run:
        class _Silent(Notifier):
            def send(self, offer, reason):  # type: ignore[override]
                log.info("DRY RUN would alert: %s %s $%.2f (%s)",
                         offer.sku, offer.condition, offer.price, reason)
                return True

        notifier = _Silent(config)
        state.save = lambda: None  # type: ignore[method-assign]

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    exit_code = 0
    while True:
        started = time.monotonic()
        try:
            result = run_once(config, client, state, notifier)
        except Exception as exc:  # noqa: BLE001 - a loop must survive one bad pass
            log.exception("pass failed: %s", exc)
            exit_code = 1
        else:
            if args.json:
                print(json.dumps({
                    "checked_skus": result.checked_skus,
                    "offers_found": result.offers_found,
                    "alerts_sent": result.alerts_sent,
                    "errors": result.errors,
                    "matches": [o.to_dict() for _, o, _ in result.matches],
                }, indent=2))
            log.info(
                "pass done in %.1fs: %s sku(s), %s offer(s), %s match(es), %s alert(s), %s error(s)",
                time.monotonic() - started, result.checked_skus, result.offers_found,
                len(result.matches), result.alerts_sent, len(result.errors),
            )
            exit_code = 1 if result.errors and not result.matches else 0

        if args.mode == "once" or _stop.is_set():
            return exit_code
        log.info("sleeping %ss", config.interval_seconds)
        if _stop.wait(config.interval_seconds):
            return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
