"""CardTrader EU price layer (fase 3b). Official API, token from the CardTrader profile → env CARDTRADER_TOKEN.

Per Fusion World expansion:
  GET /blueprints/export?expansion_id=…      → blueprint ↔ tcg_player_id / card_market_ids (the join to our canonical keys)
  GET /marketplace/products?expansion_id=…&language=en → cheapest 25 offers per blueprint (EUR, mostly EU sellers)
We keep per blueprint: lowest, median, count, EUR-only, non-graded, bundle_size 1. Raw day file is immutable.
Rate limit: 200 req / 10 s overall; marketplace endpoint ~1 req/s → we sleep 1.2 s between calls.
"""
import datetime as dt
import logging
import os
import re
import time
from statistics import median
import requests
from . import store, USER_AGENT

log = logging.getLogger("tcgwatcher.cardtrader")
BASE = "https://api.cardtrader.com/api/v2"
SETCODE = re.compile(r"\b(FB|SB|ST|FS)\s?-?(\d{2})\b", re.I)


class CardTrader:
    def __init__(self, token=None):
        self.token = (token or os.environ.get("CARDTRADER_TOKEN") or "").strip().strip('"').strip("'")
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {self.token}", "User-Agent": USER_AGENT})
        self.calls = 0

    def get(self, path, params=None, sleep=0.15):
        r = self.s.get(f"{BASE}{path}", params=params, timeout=60)
        self.calls += 1
        time.sleep(sleep)
        if r.status_code == 429:
            time.sleep(10)
            r = self.s.get(f"{BASE}{path}", params=params, timeout=60)
        if r.status_code != 200:
            raise RuntimeError(f"CardTrader {path} → HTTP {r.status_code}: {r.text[:200]}")
        data = r.json()
        # Some responses wrap the list (e.g. {"array": [...]}); errors come back as {"error": ...}
        if isinstance(data, dict):
            if "error" in data or "message" in data:
                raise RuntimeError(f"CardTrader {path} → {data}")
            for k in ("array", "data", "results"):
                if isinstance(data.get(k), list):
                    return data[k]
        return data

    def info(self):
        return self.get("/info")


def fusion_world_expansions(ct):
    games = ct.get("/games")
    if not isinstance(games, list):
        raise RuntimeError(f"unexpected /games response: {type(games).__name__} {str(games)[:200]}")
    log.info("CardTrader games: %s", [g.get("display_name") or g.get("name") for g in games][:40])
    gid = next((g["id"] for g in games if "dragon ball" in (g.get("display_name") or g.get("name") or "").lower()), None)
    if gid is None:
        raise RuntimeError("Dragon Ball game not found in CardTrader /games")
    exps = [e for e in ct.get("/expansions") if e["game_id"] == gid]
    out = {}
    for e in exps:
        m = SETCODE.search(e.get("name") or "") or SETCODE.search(e.get("code") or "")
        if m and ("fusion" in (e.get("name") or "").lower() or m.group(1).upper() in ("FB", "SB", "ST", "FS")):
            out[f"{m.group(1).upper()}{m.group(2)}"] = e
    return gid, out


def fetch_day(ct=None, codes=None):
    ct = ct or CardTrader()
    if not ct.token:
        log.info("no CARDTRADER_TOKEN; skipping")
        return None
    day = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    path = store.DATA / "cardtrader" / f"{day}.json"
    if path.exists():
        log.info("cardtrader day file exists for %s; not refetching", day)
        return store.read_json(path)
    info = ct.info()
    log.info("CardTrader auth OK: %s", {k: info.get(k) for k in ("id", "username", "name") if isinstance(info, dict) and k in info})
    gid, exps = fusion_world_expansions(ct)
    log.info("CardTrader game %s: %d Fusion World expansions matched", gid, len(exps))
    blueprints, offers = {}, {}
    for code, e in sorted(exps.items()):
        if codes and code not in codes:
            continue
        bps = ct.get("/blueprints/export", {"expansion_id": e["id"]})
        for b in bps:
            blueprints[str(b["id"])] = {"name": b.get("name"), "set": code, "category_id": b.get("category_id"),
                                       "tcg_player_id": b.get("tcg_player_id"), "card_market_ids": b.get("card_market_ids"),
                                       "image": b.get("image_url")}
        prods = ct.get("/marketplace/products", {"expansion_id": e["id"], "language": "en"}, sleep=1.2)
        for bid, lst in prods.items():
            rows = [p for p in lst if (p.get("price") or {}).get("currency") == "EUR" and not p.get("graded")
                    and (p.get("bundle_size") or 1) == 1 and not p.get("on_vacation")]
            if not rows:
                continue
            prices = sorted(p["price"]["cents"] / 100 for p in rows)
            offers[str(bid)] = {"set": code, "low": prices[0], "median": round(median(prices), 2), "n": len(prices),
                                "qty": sum(p.get("quantity") or 0 for p in rows),
                                "countries": sorted({(p.get("user") or {}).get("country_code") or "?" for p in rows})[:8],
                                "condition_low": ((rows[0].get("properties_hash") or {}).get("condition"))}
        log.info("%s: %d blueprints, %d with EN/EUR offers", code, sum(1 for b in blueprints.values() if b["set"] == code), sum(1 for o in offers.values() if o["set"] == code))
    doc = {"day": day, "fetched_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "game_id": gid,
           "expansions": {c: {"id": e["id"], "code": e.get("code"), "name": e.get("name")} for c, e in exps.items()},
           "blueprints": blueprints, "offers": offers, "api_calls": ct.calls}
    store.write_json(path, doc)
    return doc


def latest_day():
    d = store.DATA / "cardtrader"
    files = sorted(d.glob("????-??-??.json")) if d.exists() else []
    return store.read_json(files[-1]) if files else None


def eu_prices_by_tcgplayer(doc):
    """tcg_player_id → EU offer summary (joins onto our Product.product_id)."""
    if not doc:
        return {}
    out = {}
    for bid, o in doc["offers"].items():
        b = doc["blueprints"].get(bid)
        if b and b.get("tcg_player_id"):
            out[int(b["tcg_player_id"])] = {**o, "blueprint_id": int(bid), "name": b["name"]}
    return out
