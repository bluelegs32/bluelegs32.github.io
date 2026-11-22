"""Config loading: YAML file plus environment-variable overrides."""

from __future__ import annotations

import dataclasses
import os
import re
from pathlib import Path
from typing import Any

import yaml

from .model import condition_rank

_ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)(?::-([^}]*))?\}")


class ConfigError(ValueError):
    """Raised when the config file is missing required or sane values."""


def _expand(value: Any) -> Any:
    """Expand ${VAR} and ${VAR:-default} inside strings, recursively."""
    if isinstance(value, str):
        def sub(match: re.Match[str]) -> str:
            return os.environ.get(match.group(1), match.group(2) or "")

        return _ENV_PATTERN.sub(sub, value)
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand(v) for v in value]
    return value


@dataclasses.dataclass
class Watch:
    """One thing you are hunting for."""

    name: str
    skus: list[str] = dataclasses.field(default_factory=list)
    search: str | None = None
    max_price: float | None = None
    min_condition: str = "fair"
    min_discount_pct: float | None = None

    @property
    def min_condition_rank(self) -> int:
        return condition_rank(self.min_condition)

    def matches(self, offer: Any) -> tuple[bool, str]:
        """Return (matched, reason-it-was-rejected)."""
        if self.max_price is not None and offer.price > self.max_price:
            return False, f"price ${offer.price:,.2f} over cap ${self.max_price:,.2f}"
        if condition_rank(offer.condition) < self.min_condition_rank:
            return False, f"condition {offer.condition!r} below {self.min_condition!r}"
        if self.min_discount_pct is not None:
            pct = offer.discount_pct
            if pct is None or pct < self.min_discount_pct:
                return False, f"discount {pct}% below {self.min_discount_pct}%"
        return True, ""


@dataclasses.dataclass
class NtfyConfig:
    enabled: bool = False
    server: str = "https://ntfy.sh"
    topic: str = ""
    token: str | None = None
    username: str | None = None
    password: str | None = None
    priority: str = "high"


@dataclasses.dataclass
class PushoverConfig:
    enabled: bool = False
    token: str = ""
    user: str = ""
    priority: int = 1
    device: str | None = None


@dataclasses.dataclass
class Config:
    api_key: str | None = None
    zip_code: str | None = None
    store_radius_miles: int = 25
    check_store_availability: bool = False
    allow_unofficial_source: bool = False
    interval_seconds: int = 3600
    state_path: Path = Path("/data/state.json")
    repeat_after_hours: float = 24.0
    watches: list[Watch] = dataclasses.field(default_factory=list)
    ntfy: NtfyConfig = dataclasses.field(default_factory=NtfyConfig)
    pushover: PushoverConfig = dataclasses.field(default_factory=PushoverConfig)

    @property
    def any_notifier_enabled(self) -> bool:
        return self.ntfy.enabled or self.pushover.enabled


def _coerce_dataclass(cls: type, data: dict[str, Any], label: str):
    known = {f.name for f in dataclasses.fields(cls)}
    unknown = set(data) - known
    if unknown:
        raise ConfigError(f"{label}: unknown option(s): {', '.join(sorted(unknown))}")
    return cls(**data)


def load_config(path: str | Path) -> Config:
    """Read a YAML config, expand env vars, validate, and return a Config."""
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")

    raw = _expand(yaml.safe_load(path.read_text()) or {})
    if not isinstance(raw, dict):
        raise ConfigError(f"config file must contain a YAML mapping: {path}")

    watches_raw = raw.pop("watches", []) or []
    notify_raw = raw.pop("notify", {}) or {}
    raw.pop("_comment", None)

    cfg = _coerce_dataclass(Config, raw, "top level")
    cfg.state_path = Path(cfg.state_path)

    for i, item in enumerate(watches_raw):
        if not isinstance(item, dict):
            raise ConfigError(f"watches[{i}] must be a mapping")
        watch = _coerce_dataclass(Watch, dict(item), f"watches[{i}]")
        if not watch.skus and not watch.search:
            raise ConfigError(f"watches[{i}] ({watch.name!r}): set 'skus' or 'search'")
        watch.skus = [str(s).strip() for s in watch.skus if str(s).strip()]
        cfg.watches.append(watch)

    if "ntfy" in notify_raw:
        cfg.ntfy = _coerce_dataclass(NtfyConfig, dict(notify_raw["ntfy"] or {}), "notify.ntfy")
    if "pushover" in notify_raw:
        cfg.pushover = _coerce_dataclass(
            PushoverConfig, dict(notify_raw["pushover"] or {}), "notify.pushover"
        )

    _apply_env_overrides(cfg)
    _validate(cfg)
    return cfg


def _apply_env_overrides(cfg: Config) -> None:
    """Env vars win over the file, so Fly secrets / Unraid vars work unedited."""
    env = os.environ
    if env.get("BESTBUY_API_KEY"):
        cfg.api_key = env["BESTBUY_API_KEY"]
    if env.get("STATE_PATH"):
        cfg.state_path = Path(env["STATE_PATH"])
    if env.get("INTERVAL_SECONDS"):
        cfg.interval_seconds = int(env["INTERVAL_SECONDS"])
    if env.get("ZIP_CODE"):
        cfg.zip_code = env["ZIP_CODE"]

    if env.get("NTFY_TOPIC"):
        cfg.ntfy.topic = env["NTFY_TOPIC"]
        cfg.ntfy.enabled = True
    if env.get("NTFY_SERVER"):
        cfg.ntfy.server = env["NTFY_SERVER"]
    if env.get("NTFY_TOKEN"):
        cfg.ntfy.token = env["NTFY_TOKEN"]

    if env.get("PUSHOVER_TOKEN") and env.get("PUSHOVER_USER"):
        cfg.pushover.token = env["PUSHOVER_TOKEN"]
        cfg.pushover.user = env["PUSHOVER_USER"]
        cfg.pushover.enabled = True


def _validate(cfg: Config) -> None:
    if not cfg.watches:
        raise ConfigError("no watches configured - nothing to look for")
    if cfg.ntfy.enabled and not cfg.ntfy.topic:
        raise ConfigError("notify.ntfy.enabled is true but no topic is set")
    if cfg.pushover.enabled and not (cfg.pushover.token and cfg.pushover.user):
        raise ConfigError("notify.pushover.enabled is true but token/user are missing")
    if cfg.check_store_availability and not cfg.zip_code:
        raise ConfigError("check_store_availability requires zip_code")
    if cfg.interval_seconds < 60:
        raise ConfigError("interval_seconds must be at least 60")
