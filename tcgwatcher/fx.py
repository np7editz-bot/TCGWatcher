"""USD→EUR rate per day (ECB via frankfurter.dev). Stored under data/fx/<day>.json; falls back to the last stored rate."""
import datetime as dt
import logging
import requests
from . import store, USER_AGENT

log = logging.getLogger("tcgwatcher.fx")


def usd_eur(day):
    cached = store.read_json(store.fx_path(day))
    if cached:
        return cached["rate"], cached
    try:
        r = requests.get("https://api.frankfurter.dev/v1/latest", params={"from": "USD", "to": "EUR"},
                         headers={"User-Agent": USER_AGENT}, timeout=30)
        r.raise_for_status()
        j = r.json()
        doc = {"rate": j["rates"]["EUR"], "rate_date": j["date"], "source": "ECB via frankfurter.dev",
               "fetched_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
        store.write_json(store.fx_path(day), doc, compact=False)
        return doc["rate"], doc
    except Exception as e:  # noqa: BLE001
        log.warning("fx fetch failed (%s); using last known rate", e)
        d = store.DATA / "fx"
        files = sorted(d.glob("????-??-??.json")) if d.exists() else []
        if files:
            doc = store.read_json(files[-1])
            doc = dict(doc, stale=True)
            return doc["rate"], doc
        return 0.9, {"rate": 0.9, "source": "fallback constant", "stale": True}
