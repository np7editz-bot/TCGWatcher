"""Cardmarket price guide (official, public, daily) — the EU sales-based price next to CardTrader's asks.

Cardmarket publishes, for every game, three JSON files on its CDN (no key, no bot-wall):
  productCatalog/productList/products_singles_<game>.json     idProduct, name, idExpansion, idMetacard
  productCatalog/productList/products_nonsingles_<game>.json  sealed: boxes, packs, decks, cases
  productCatalog/priceGuide/price_guide_<game>.json           per idProduct: low (cheapest offer), trend, avg1/avg7/avg30
                                                              (average SALE price over 1/7/30 days), *-foil variants
Game 13 = Dragon Ball Super (incl. Fusion World). Cards are joined through CardTrader's blueprint `card_market_ids`;
sealed boxes through the product name. Only the rows we use are kept per day (data/cardmarket/<day>.json, immutable).
"""
import datetime as dt
import logging
import re
import requests
from . import store, USER_AGENT

log = logging.getLogger("tcgwatcher.cardmarket")
CDN = "https://downloads.s3.cardmarket.com/productCatalog"
GAME_IDS = {"dbfw": 13, "dbs": 13}
NOT_EN = re.compile(r"(asia|non-english|japanese|\bjp\b|korean|chinese)", re.I)


def fetch_files(game_id=13, session=None):
    s = session or requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    out = {}
    for key, path in (("singles", f"productList/products_singles_{game_id}.json"),
                      ("nonsingles", f"productList/products_nonsingles_{game_id}.json"),
                      ("prices", f"priceGuide/price_guide_{game_id}.json")):
        r = s.get(f"{CDN}/{path}", timeout=120)
        r.raise_for_status()
        out[key] = r.json()
    return out


def slug(name):
    """Cardmarket URL slug: apostrophes dropped, every other non-alphanumeric run becomes '-' ("Saiyan's Pride" → Saiyans-Pride)."""
    return re.sub(r"[^A-Za-z0-9]+", "-", name.replace("'", "")).strip("-")


def box_url(name):
    """Sealed product page: /Products/Booster-Boxes/<Name-Slug> (name 'Awakened Pulse Booster Box [Fusion World]' →
    Awakened-Pulse-Booster-Box-Fusion-World)."""
    return "https://www.cardmarket.com/en/DragonBallSuper/Products/Booster-Boxes/" + slug(name)


def _f(row, field):
    """Price-guide value with the foil fallback: Cardmarket keeps `low/trend/avg*` for the non-foil version and
    `*-foil` for the foil one; Fusion World hits are foil-only, so their sales live in the foil fields (0/None elsewhere)."""
    v = row.get(field)
    return v if v else row.get(f"{field}-foil")


def join(files, latest, ct_doc):
    """Keep only what we use: price-guide rows for our chase cards (via CardTrader card_market_ids) and sealed boxes."""
    prices = {p["idProduct"]: p for p in files["prices"].get("priceGuides", [])}
    cm_ids_by_tcg = {}
    for b in ((ct_doc or {}).get("blueprints") or {}).values():
        if b.get("tcg_player_id") and b.get("card_market_ids"):
            cm_ids_by_tcg[int(b["tcg_player_id"])] = [int(x) for x in b["card_market_ids"] if x]
    cards, boxes = {}, {}
    for gid, g in latest["games"].items():
        for c in g.get("cards", []):
            ids = cm_ids_by_tcg.get(c.get("product_id"))
            if not ids:
                continue
            rows = [prices[i] for i in ids if i in prices]
            if not rows:
                continue
            r = min(rows, key=lambda x: _f(x, "low") or 9e9)         # several Cardmarket products can map to one card (versions)
            cards[c["key"]] = {"idProduct": r["idProduct"], "low": _f(r, "low"), "trend": _f(r, "trend"),
                               "avg1": _f(r, "avg1"), "avg7": _f(r, "avg7"), "avg30": _f(r, "avg30"),
                               "foil": not r.get("avg30") and bool(r.get("avg30-foil"))}
        sealed = [p for p in files["nonsingles"].get("products", []) if "Booster Box" in p.get("name", "")]
        for s in g.get("sets", []):
            name = (s.get("name") or "").lower()
            if not name:
                continue
            hits = [p for p in sealed if name in p["name"].lower() and "case" not in p["name"].lower() and not NOT_EN.search(p["name"])]
            if gid == "dbfw":
                hits = [p for p in hits if "fusion world" in p["name"].lower()] or hits
            else:
                hits = [p for p in hits if "fusion world" not in p["name"].lower()]
            rows = [(p, prices.get(p["idProduct"])) for p in hits]
            rows = [(p, r) for p, r in rows if r and r.get("low")]
            if rows:
                p, r = min(rows, key=lambda x: x[1]["low"])
                boxes[s["code"]] = {"idProduct": p["idProduct"], "name": p["name"], "low": r["low"], "trend": r.get("trend"),
                                    "avg7": r.get("avg7"), "avg30": r.get("avg30"),
                                    "url": box_url(p["name"])}
    return cards, boxes


def fetch_day(latest=None, ct_doc=None, day=None, session=None):
    day = day or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    path = store.DATA / "cardmarket" / f"{day}.json"
    if path.exists():
        log.info("cardmarket day file exists for %s; not refetching", day)
        return store.read_json(path)
    from .build import LATEST
    from .cardtrader import latest_day as ct_latest
    latest = latest or store.read_json(LATEST)
    ct_doc = ct_doc if ct_doc is not None else ct_latest()
    files = fetch_files(13, session)
    cards, boxes = join(files, latest, ct_doc)
    doc = {"day": day, "fetched_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "game_id": 13,
           "guide_created_at": files["prices"].get("createdAt"), "cards": cards, "boxes": boxes,
           "note": "low = cheapest offer; avg30 = average sale price last 30 days (EUR). Source: Cardmarket public price guide."}
    store.write_json(path, doc)
    log.info("cardmarket: %d cards, %d boxes joined (guide %s)", len(cards), len(boxes), doc["guide_created_at"])
    return doc


def latest_day():
    d = store.DATA / "cardmarket"
    files = sorted(p.name for p in d.glob("????-??-??.json")) if d.exists() else []
    return store.read_json(d / files[-1]) if files else None
