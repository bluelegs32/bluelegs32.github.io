# bbwatch — Best Buy open-box watcher

Polls Best Buy for open-box listings of the products you care about and pushes a
phone notification the moment one shows up at or below your price. One codebase,
two deployments: a Fly.io machine, an Unraid container, or both.

It replaces the "scheduled ChatGPT search" approach with something that actually
runs on a schedule, never misses a window, and only pings you once per find.

---

## What it does

- Checks the **official Best Buy Open Box API** for every SKU you list, every hour.
- Optionally resolves a **keyword search** to SKUs each pass, so new model numbers
  get picked up without you editing config.
- Filters on **price cap**, **minimum condition grade**, and **minimum discount**.
- Remembers what it already told you. You get one alert per listing, plus a fresh
  one if the price drops, plus an optional daily reminder while it is still up.
- Sends to **ntfy** and/or **Pushover**, with a tap-through link straight to the
  product page.

Condition grades rank: `fair` < `satisfactory`/`good` < `excellent` < `certified`.

---

## 1. Get a Best Buy API key

Free, takes a few minutes: <https://developer.bestbuy.com>. Sign up, create an
app, and request access to the **Open Box API** (it is a beta endpoint, requested
separately from the main product API). Standard limits are 5 requests/second and
50,000/day — hourly polling of a handful of SKUs uses a rounding error of that.

## 2. Find your SKUs

The SKU is in every Best Buy product URL: `bestbuy.com/site/.../6618354.p` →
`6618354`. Put those in `skus`. The example config has a placeholder SKU for the
97" G5 — **confirm it against the live product page**, model numbers shift between
panel revisions.

## 3. Set up notifications

**ntfy** (free, no account): pick an unguessable topic name like
`bb-openbox-k3n8xq2`, install the ntfy app, subscribe to that topic. Anyone who
guesses the topic can read it, so do not use `bestbuy`. If you already run ntfy on
Unraid, point `server` at `http://<unraid-ip>:8080` and use a token instead.

**Pushover** ($5 one-time): create an application at pushover.net to get a token,
and copy your user key from the dashboard. Set `priority: 2` for emergency alerts
that repeat until you acknowledge them — reasonable for a $2k TV, obnoxious for
anything else.

---

## Deploy: Fly.io

Two shapes. **Always-on worker** is simpler and costs roughly a dollar a month on
a `shared-cpu-1x` 256MB machine; **scheduled machine** costs essentially nothing
but only runs on Fly's coarse schedule (`hourly`/`daily`).

### A. Always-on worker (recommended)

```bash
cd bestbuy-watcher
fly launch --no-deploy --copy-config --name bbwatch      # or pick your own name
fly volumes create bbwatch_data --size 1 --region ord

fly secrets set \
  BESTBUY_API_KEY=xxxxxxxxxxxxxxxx \
  NTFY_TOPIC=bb-openbox-k3n8xq2
# optional: NTFY_TOKEN=... PUSHOVER_TOKEN=... PUSHOVER_USER=...

fly deploy
```

The config file lives on the volume at `/data/config.yml`. The entrypoint seeds it
from `config.example.yml` on first boot, so edit it in place and restart:

```bash
fly ssh console -C "vi /data/config.yml"   # or: fly ssh sftp shell
fly machine restart
fly logs
```

Make sure exactly one machine is running — two machines mean two sets of alerts:

```bash
fly scale count 1
```

### B. Scheduled machine

```bash
fly volumes create bbwatch_data --size 1 --region ord
fly deploy --no-public-ips
fly machine run . --schedule hourly --env RUN_MODE=once \
  --volume bbwatch_data:/data --region ord
```

`RUN_MODE=once` makes the process do a single pass and exit. State persists on the
volume between runs, so de-duplication still works.

### Test it

```bash
fly ssh console -C "python -m bbwatch.cli --test-notify"
fly ssh console -C "python -m bbwatch.cli --mode once --dry-run -v"
```

---

## Deploy: Unraid

### Option A — Docker Compose Manager plugin

```bash
mkdir -p /mnt/user/appdata/bbwatch/{config,data}
cd /mnt/user/appdata/bbwatch && git clone <this repo> src
cd src/bestbuy-watcher
cp .env.example .env && nano .env      # put your key and topic here
docker compose up -d --build
```

### Option B — Docker tab template

1. Build the image once on the server: `docker build -t bbwatch:latest .`
2. Copy `unraid-template.xml` to
   `/boot/config/plugins/dockerMan/templates-user/my-bbwatch.xml`
3. **Docker → Add Container →** pick `bbwatch` from the template dropdown, fill in
   the API key and ntfy topic, Apply.

Either way, the container seeds `/mnt/user/appdata/bbwatch/config/config.yml` on
first start and then exits-loops on the interval. Edit that file, restart the
container, and watch the logs from the Docker tab.

### Test it

```bash
docker exec bbwatch python -m bbwatch.cli --test-notify
docker exec bbwatch python -m bbwatch.cli --mode once --dry-run -v
```

---

## Running both

Harmless, and a reasonable belt-and-braces setup — Unraid for the cheap always-on
poll, Fly as the backstop for when your home internet or array is down. They keep
separate state files, so you will get **two** notifications per find. If that
bothers you, give them different `repeat_after_hours` and point them at different
ntfy topics, or run Fly with a narrower watch list (just the one TV) and Unraid
with the full list.

---

## Configuration

See `config.example.yml` — every key is commented. Highlights:

| Key | Meaning |
| --- | --- |
| `watches[].skus` | Exact SKUs to poll. The reliable path. |
| `watches[].search` | Keywords resolved to SKUs each pass. Catches new listings, noisier. |
| `watches[].max_price` | Hard cap. Nothing above this ever alerts. |
| `watches[].min_condition` | `fair`, `satisfactory`, `excellent`, `certified`. |
| `watches[].min_discount_pct` | Skip the 10%-off "open box" listings that are not deals. |
| `interval_seconds` | 3600 is plenty. Below 300 is refused. |
| `repeat_after_hours` | Re-ping about a still-live listing. `0` disables reminders. |
| `check_store_availability` | Adds nearby stores to the alert. Needs `zip_code`. |
| `allow_unofficial_source` | Off by default — see the caveat below. |

Any `${VAR}` in the file is filled from the environment, and these env vars
override the file outright: `BESTBUY_API_KEY`, `NTFY_TOPIC`, `NTFY_SERVER`,
`NTFY_TOKEN`, `PUSHOVER_TOKEN`, `PUSHOVER_USER`, `ZIP_CODE`, `INTERVAL_SECONDS`,
`STATE_PATH`, `CONFIG_PATH`, `RUN_MODE`.

---

## Caveats worth knowing before you rely on it

- **Store-level open-box stock is not in the API.** The Open Box API reports
  offers and grades; `check_store_availability` uses the separate Stores API,
  which tracks *new* inventory. So the alert tells you a unit exists at a price —
  it does not promise the one 25 minutes from you. For the deal in that post you
  would still have been the one driving over to look at it.
- **The $2,000 outcome came from a register re-ring**, not from the listing. No
  watcher can see that. What this buys you is being first to the listing.
- `allow_unofficial_source` reads the JSON endpoint bestbuy.com's own product page
  calls. It is undocumented, unsupported, and may be blocked or changed without
  notice. The official API covers this use case; leave it off unless you have a
  reason.
- Polling faster than hourly will not find much that hourly misses and does put
  you closer to rate limits. The floor is 300 seconds and it is there on purpose.

---

## Development

```bash
pip install -r requirements.txt pytest
PYTHONPATH=src python -m pytest tests -q
PYTHONPATH=src python -m bbwatch.cli -c config.yml --mode once --dry-run -v
```

Tests cover config validation, condition ranking, offer parsing against sample API
payloads, price-drop and reminder logic in the state store, notification payload
shape, and an end-to-end run with the network faked. No test touches the network.

```
src/bbwatch/
  model.py    Offer record, condition grading
  config.py   YAML + env loading and validation
  sources.py  Best Buy API client, open-box / search / stores
  state.py    alert history, atomic writes
  notify.py   ntfy + Pushover
  runner.py   one pass: fetch, match, de-dupe, notify
  cli.py      once/loop modes, signals, --test-notify, --dry-run
```
