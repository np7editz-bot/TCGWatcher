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
EU = {"AT","BE","BG","HR","CY","CZ","DK","EE","FI","FR","DE","GR","HU","IE","IT","LV","LT","LU","MT","NL","PL","PT","RO","SK","SI","ES","SE","CH","NO"}   # = ebay.EU; GB out (import VAT)
SETCODE = re.compile(r"\b(FB|SB|ST|FS|BT|BE|SD|EB)\s?-?(\d{2})\b", re.I)


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


def watched_expansions(ct):
    """Expansions to fetch, keyed by our set code, across every game with `eu_sources`.
    Game match: `cardtrader_game` substring of the CardTrader game name (Dragon Ball runs Fusion World and the original
    line as two games: both). Expansion match: `cardtrader_expansions` regex on the CardTrader expansion code (Riftbound:
    ogn/sfd/…), else the Dragon Ball SETCODE regex on name/code (FB01, BT26 …)."""
    from .config import load_games
    games = ct.get("/games")
    if not isinstance(games, list):
        raise RuntimeError(f"unexpected /games response: {type(games).__name__} {str(games)[:200]}")
    log.info("CardTrader games: %s", [g.get("display_name") or g.get("name") for g in games][:40])
    all_exps = ct.get("/expansions")
    out, gids = {}, []
    for gid_our, g in load_games().items():
        if not g.get("eu_sources"):
            continue
        needle = (g.get("cardtrader_game") or "dragon ball").lower()
        ids = [x["id"] for x in games if needle in (x.get("display_name") or x.get("name") or "").lower()]
        if not ids:
            log.warning("CardTrader: no game matching %r for %s", needle, gid_our)
            continue
        gids += [i for i in ids if i not in gids]
        rx = re.compile(g["cardtrader_expansions"], re.I) if g.get("cardtrader_expansions") else None
        for e in all_exps:
            if e["game_id"] not in ids:
                continue
            if rx:
                if rx.search(e.get("code") or ""):
                    out.setdefault((e.get("code") or "").upper(), e)
            else:
                m = SETCODE.search(e.get("name") or "") or SETCODE.search(e.get("code") or "")
                if m and m.group(1).upper() in ("FB", "SB", "ST", "FS", "BT", "BE", "SD", "EB"):
                    out.setdefault(f"{m.group(1).upper()}{m.group(2)}", e)
    if not gids:
        raise RuntimeError("no CardTrader game matched any configured game")
    log.info("CardTrader game ids: %s", gids)
    return gids[0], out


fusion_world_expansions = watched_expansions      # old name


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
    gid, exps = watched_expansions(ct)
    log.info("CardTrader: %d expansions matched across games", len(exps))
    blueprints, offers, graded = {}, {}, {}
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
            eu_rows = [p for p in lst if (p.get("price") or {}).get("currency") == "EUR" and (p.get("bundle_size") or 1) == 1
                       and not p.get("on_vacation") and ((p.get("user") or {}).get("country_code") or "").upper() in EU]
            # graded copies (slabs) are a separate market: kept per listing with whatever grading info the seller filled in
            slabs = [{"price": p["price"]["cents"] / 100, "zero": bool((p.get("user") or {}).get("can_sell_via_hub")),
                      "country": (p.get("user") or {}).get("country_code"), "id": p.get("id"),
                      "props": {k: v for k, v in (p.get("properties_hash") or {}).items() if v not in (None, "", False)},
                      "description": (p.get("description") or "")[:120]}
                     for p in eu_rows if p.get("graded")]
            if slabs:
                graded[str(bid)] = {"set": code, "listings": sorted(slabs, key=lambda x: x["price"])[:20]}
            rows = [p for p in eu_rows if not p.get("graded")]
            if not rows:
                continue
            prices = sorted(p["price"]["cents"] / 100 for p in rows)
            # CardTrader Zero: seller ships to the CardTrader hub, buyer pays one fixed fee — no per-seller shipping surprise.
            zero = sorted(p["price"]["cents"] / 100 for p in rows if (p.get("user") or {}).get("can_sell_via_hub"))
            offers[str(bid)] = {"set": code, "low": prices[0], "median": round(median(prices), 2), "n": len(prices),
                                "zero_low": zero[0] if zero else None, "zero_n": len(zero),
                                "qty": sum(p.get("quantity") or 0 for p in rows),
                                "countries": sorted({(p.get("user") or {}).get("country_code") or "?" for p in rows})[:8],
                                "condition_low": ((rows[0].get("properties_hash") or {}).get("condition"))}
        log.info("%s: %d blueprints, %d with EN/EUR offers", code, sum(1 for b in blueprints.values() if b["set"] == code), sum(1 for o in offers.values() if o["set"] == code))
    doc = {"day": day, "fetched_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "game_id": gid,
           "expansions": {c: {"id": e["id"], "code": e.get("code"), "name": e.get("name")} for c, e in exps.items()},
           "blueprints": blueprints, "offers": offers, "graded": graded, "api_calls": ct.calls}
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
            out[int(b["tcg_player_id"])] = {**o, "blueprint_id": int(bid), "name": b["name"], "image": b.get("image")}
    return out
