import json
import time
from pathlib import Path

import pytest

from bbwatch.config import ConfigError, load_config
from bbwatch.model import Offer, condition_rank
from bbwatch.notify import Notifier, format_body, format_title
from bbwatch.runner import run_once
from bbwatch.sources import BestBuyClient, SourceError, _parse_offers, fetch_open_box, search_skus
from bbwatch.state import State

from fixtures import OPEN_BOX_RESPONSE, PRODUCT_SEARCH_RESPONSE, STORES_RESPONSE


class FakeClient(BestBuyClient):
    """BestBuyClient with the network swapped for canned payloads."""

    def __init__(self, responses, api_key="k"):
        super().__init__(api_key, min_interval=0)
        self.responses = responses
        self.calls = []

    def get_json(self, url, params=None):
        self.calls.append((url, params))
        for fragment, payload in self.responses.items():
            if fragment in url:
                if isinstance(payload, Exception):
                    raise payload
                return payload
        raise AssertionError(f"unexpected request: {url}")


def write_config(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "config.yml"
    path.write_text(body)
    return path


BASE_CONFIG = """
api_key: testkey
state_path: {state}
interval_seconds: 600
watches:
  - name: G5
    skus: ["6618354"]
    max_price: 6000
    min_condition: excellent
notify:
  ntfy:
    enabled: true
    topic: secret-topic
"""


# --- model ---------------------------------------------------------------

def test_condition_rank_orders_grades():
    assert condition_rank("certified") > condition_rank("excellent")
    assert condition_rank("excellent") > condition_rank("good")
    assert condition_rank("good") == condition_rank("satisfactory")
    assert condition_rank("nonsense") == 0
    assert condition_rank(None) == 0


def test_discount_pct_and_key():
    offer = Offer(sku="1", name="x", condition="Excellent", price=2000, regular_price=25000)
    assert offer.discount_pct == 92.0
    assert offer.key == "1:excellent"
    assert Offer(sku="1", name="x", condition="e", price=5).discount_pct is None


# --- config --------------------------------------------------------------

def test_load_config_expands_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_TOPIC", "from-env")
    path = write_config(tmp_path, """
api_key: k
state_path: /tmp/s.json
watches:
  - name: a
    skus: [1]
notify:
  ntfy: {enabled: true, topic: "${MY_TOPIC}"}
""")
    cfg = load_config(path)
    assert cfg.ntfy.topic == "from-env"
    assert cfg.watches[0].skus == ["1"]


def test_env_default_syntax(tmp_path, monkeypatch):
    monkeypatch.delenv("NOPE", raising=False)
    path = write_config(tmp_path, """
api_key: k
state_path: /tmp/s.json
zip_code: "${NOPE:-60601}"
watches: [{name: a, skus: [1]}]
notify: {ntfy: {enabled: true, topic: t}}
""")
    assert load_config(path).zip_code == "60601"


def test_env_overrides_file(tmp_path, monkeypatch):
    monkeypatch.setenv("BESTBUY_API_KEY", "env-key")
    monkeypatch.setenv("NTFY_TOPIC", "env-topic")
    path = write_config(tmp_path, BASE_CONFIG.format(state=tmp_path / "s.json"))
    cfg = load_config(path)
    assert cfg.api_key == "env-key"
    assert cfg.ntfy.topic == "env-topic"


def test_config_rejects_unknown_key(tmp_path):
    path = write_config(tmp_path, """
api_key: k
typo_here: 1
watches: [{name: a, skus: [1]}]
notify: {ntfy: {enabled: true, topic: t}}
""")
    with pytest.raises(ConfigError, match="typo_here"):
        load_config(path)


def test_config_rejects_watch_without_target(tmp_path):
    path = write_config(tmp_path, """
api_key: k
watches: [{name: a, max_price: 10}]
notify: {ntfy: {enabled: true, topic: t}}
""")
    with pytest.raises(ConfigError, match="skus"):
        load_config(path)


def test_config_rejects_enabled_notifier_without_topic(tmp_path):
    path = write_config(tmp_path, """
api_key: k
watches: [{name: a, skus: [1]}]
notify: {ntfy: {enabled: true}}
""")
    with pytest.raises(ConfigError, match="topic"):
        load_config(path)


def test_config_rejects_store_check_without_zip(tmp_path, monkeypatch):
    monkeypatch.delenv("ZIP_CODE", raising=False)
    path = write_config(tmp_path, """
api_key: k
check_store_availability: true
watches: [{name: a, skus: [1]}]
notify: {ntfy: {enabled: true, topic: t}}
""")
    with pytest.raises(ConfigError, match="zip_code"):
        load_config(path)


# --- matching ------------------------------------------------------------

def make_watch(**kwargs):
    from bbwatch.config import Watch
    return Watch(name="w", **kwargs)


def test_watch_filters():
    offer = Offer(sku="1", name="n", condition="excellent", price=2000, regular_price=25000)
    assert make_watch(skus=["1"], max_price=3000).matches(offer)[0]
    assert not make_watch(skus=["1"], max_price=1000).matches(offer)[0]
    assert not make_watch(skus=["1"], min_condition="certified").matches(offer)[0]
    assert make_watch(skus=["1"], min_discount_pct=90).matches(offer)[0]
    assert not make_watch(skus=["1"], min_discount_pct=95).matches(offer)[0]


def test_watch_rejects_unknown_condition_when_grade_required():
    offer = Offer(sku="1", name="n", condition="mystery", price=10)
    ok, why = make_watch(skus=["1"], min_condition="excellent").matches(offer)
    assert not ok and "mystery" in why


# --- sources -------------------------------------------------------------

def test_parse_offers_skips_priceless_entries():
    offers = _parse_offers(OPEN_BOX_RESPONSE["results"][0], "bestbuy-api")
    assert [o.condition for o in offers] == ["excellent", "fair"]
    assert offers[0].price == 2000.0
    assert offers[0].regular_price == 24999.99
    assert offers[0].online_available is True
    assert offers[0].url.endswith("condition=excellent")


def test_parse_offers_falls_back_to_search_url():
    result = {"sku": 999, "offers": [{"condition": "fair", "prices": {"current": 5}}]}
    offer = _parse_offers(result, "s")[0]
    assert offer.name == "SKU 999"
    assert "searchpage" in offer.url


def test_fetch_open_box_batches_skus():
    client = FakeClient({"openBox": OPEN_BOX_RESPONSE})
    offers = fetch_open_box(client, [str(i) for i in range(45)])
    assert len(client.calls) == 3  # 20 + 20 + 5
    assert len(offers) == 6


def test_fetch_open_box_requires_key():
    with pytest.raises(SourceError, match="API key"):
        fetch_open_box(BestBuyClient(None), ["1"])


def test_search_skus():
    client = FakeClient({"/v1/products": PRODUCT_SEARCH_RESPONSE})
    assert search_skus(client, "LG OLED 97") == ["6618354", "6577100"]
    url = client.calls[0][0]
    assert "search=LG" in url and "search=OLED" in url


def test_store_availability():
    from bbwatch.sources import fetch_store_availability
    client = FakeClient({"/v1/stores": STORES_RESPONSE})
    assert fetch_store_availability(client, "1", "60601") == [
        "Schaumburg (7.4 mi)", "Niles (12.1 mi)"
    ]


def test_store_availability_swallows_errors():
    from bbwatch.sources import fetch_store_availability
    client = FakeClient({"/v1/stores": SourceError("boom")})
    assert fetch_store_availability(client, "1", "60601") == []


# --- state ---------------------------------------------------------------

def test_state_alerts_once_then_suppresses(tmp_path):
    state = State(tmp_path / "s.json", repeat_after_hours=24)
    offer = Offer(sku="1", name="n", condition="excellent", price=2000)
    assert state.is_new(offer)[0]
    state.record(offer, alerted=True)
    assert not state.is_new(offer)[0]


def test_state_realerts_on_price_drop(tmp_path):
    state = State(tmp_path / "s.json")
    state.record(Offer(sku="1", name="n", condition="excellent", price=2000), alerted=True)
    cheaper = Offer(sku="1", name="n", condition="excellent", price=1500)
    ok, why = state.is_new(cheaper)
    assert ok and "dropped" in why


def test_state_reminder_after_window(tmp_path):
    state = State(tmp_path / "s.json", repeat_after_hours=1)
    offer = Offer(sku="1", name="n", condition="excellent", price=2000)
    state.record(offer, alerted=True, now=time.time() - 7200)
    ok, why = state.is_new(offer)
    assert ok and "reminder" in why


def test_state_round_trips_and_survives_corruption(tmp_path):
    path = tmp_path / "s.json"
    state = State(path)
    state.record(Offer(sku="1", name="n", condition="excellent", price=2000), alerted=True)
    state.save()
    assert json.loads(path.read_text())["version"] == 1
    assert State(path).seen["1:excellent"]["price"] == 2000

    path.write_text("{not json")
    assert State(path).seen == {}


def test_state_prune_drops_stale(tmp_path):
    state = State(tmp_path / "s.json")
    old = Offer(sku="old", name="n", condition="fair", price=1)
    new = Offer(sku="new", name="n", condition="fair", price=1)
    state.record(old, alerted=True, now=time.time() - 200 * 86400)
    state.record(new, alerted=True)
    state.prune({new.key})
    assert set(state.seen) == {"new:fair"}


# --- notify --------------------------------------------------------------

def test_message_formatting():
    offer = Offer(sku="6618354", name='LG 97" G5', condition="excellent",
                  price=2000, regular_price=24999.99, online_available=True,
                  stores=("Schaumburg (7.4 mi)",))
    assert "$2,000 open-box Excellent" in format_title(offer)
    body = format_body(offer, "new listing")
    assert "92.0% off" in body
    assert "Schaumburg" in body
    assert "SKU 6618354 - new listing" in body


class FakeSession:
    def __init__(self, status=200):
        self.status = status
        self.posts = []

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        return self

    def raise_for_status(self):
        if self.status >= 400:
            import requests
            raise requests.HTTPError(f"HTTP {self.status}")


def test_notifier_sends_to_enabled_channels(tmp_path, monkeypatch):
    monkeypatch.delenv("PUSHOVER_TOKEN", raising=False)
    path = write_config(tmp_path, """
api_key: k
watches: [{name: a, skus: [1]}]
notify:
  ntfy: {enabled: true, topic: mytopic, token: tok}
  pushover: {enabled: true, token: pt, user: pu}
""")
    cfg = load_config(path)
    session = FakeSession()
    notifier = Notifier(cfg, session=session)
    offer = Offer(sku="1", name="n", condition="excellent", price=2000,
                  url="https://example.test/x")
    assert notifier.send(offer, "new listing")

    ntfy_url, ntfy_kwargs = session.posts[0]
    assert ntfy_url == "https://ntfy.sh/mytopic"
    assert ntfy_kwargs["headers"]["Authorization"] == "Bearer tok"
    assert ntfy_kwargs["headers"]["Click"] == "https://example.test/x"

    po_url, po_kwargs = session.posts[1]
    assert "pushover.net" in po_url
    assert po_kwargs["data"]["user"] == "pu"


def test_notifier_reports_total_failure(tmp_path):
    path = write_config(tmp_path, """
api_key: k
watches: [{name: a, skus: [1]}]
notify: {ntfy: {enabled: true, topic: t}}
""")
    notifier = Notifier(load_config(path), session=FakeSession(status=500))
    assert not notifier.send(Offer(sku="1", name="n", condition="e", price=1), "x")


# --- runner --------------------------------------------------------------

def test_run_once_end_to_end(tmp_path, monkeypatch):
    monkeypatch.delenv("BESTBUY_API_KEY", raising=False)
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    path = write_config(tmp_path, BASE_CONFIG.format(state=tmp_path / "s.json"))
    cfg = load_config(path)
    client = FakeClient({"openBox": OPEN_BOX_RESPONSE})
    state = State(cfg.state_path, cfg.repeat_after_hours)
    session = FakeSession()
    notifier = Notifier(cfg, session=session)

    result = run_once(cfg, client, state, notifier)
    # Two offers parsed; only "excellent" clears min_condition and max_price.
    assert result.offers_found == 2
    assert len(result.matches) == 1
    assert result.alerts_sent == 1
    assert result.matches[0][1].condition == "excellent"
    assert len(session.posts) == 1

    # Second pass: nothing new, no second ping.
    again = run_once(cfg, client, state, Notifier(cfg, session=session))
    assert again.alerts_sent == 0
    assert len(session.posts) == 1
    assert cfg.state_path.exists()


def test_run_once_records_source_errors(tmp_path, monkeypatch):
    monkeypatch.delenv("BESTBUY_API_KEY", raising=False)
    path = write_config(tmp_path, BASE_CONFIG.format(state=tmp_path / "s.json"))
    cfg = load_config(path)
    client = FakeClient({"openBox": SourceError("api down")})
    result = run_once(cfg, client, State(cfg.state_path), Notifier(cfg, session=FakeSession()))
    assert result.errors and "api down" in result.errors[0]
    assert not result.matches


def test_run_once_adds_store_info(tmp_path, monkeypatch):
    monkeypatch.delenv("BESTBUY_API_KEY", raising=False)
    path = write_config(tmp_path, """
api_key: k
zip_code: "60601"
check_store_availability: true
state_path: {state}
watches: [{{name: G5, skus: ["6618354"], min_condition: excellent}}]
notify: {{ntfy: {{enabled: true, topic: t}}}}
""".format(state=tmp_path / "s.json"))
    cfg = load_config(path)
    client = FakeClient({"openBox": OPEN_BOX_RESPONSE, "/v1/stores": STORES_RESPONSE})
    result = run_once(cfg, client, State(cfg.state_path), Notifier(cfg, session=FakeSession()))
    matched = [o for _, o, _ in result.matches]
    assert any(o.stores for o in matched)
