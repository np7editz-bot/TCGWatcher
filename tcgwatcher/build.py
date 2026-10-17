"""Build step: scores + trends → site/data/latest.json (what the page reads) with provenance on every block.

Refuses to publish when the quality gate failed (keeps the previous latest.json) — a wrong page is worse than a stale one.
"""
import datetime as dt
import logging
import re
from .config import load_games
from .model import load_game, class_estimates
from .chase import score_sets
from .history import load_series, change_pct, sparkline
from .fx import usd_eur
from . import store, SITE

log = logging.getLogger("tcgwatcher.build")
LATEST = SITE / "data" / "latest.json"


def build(day, quality=None):
    quality = quality or store.read_json(store.quality_path(day), {"status": "unknown", "games": {}})
    if quality.get("status") == "fail":
        log.error("quality gate failed for %s — not publishing; previous latest.json kept", day)
        return None
    rate, fx_doc = usd_eur(day)
    games = load_games()
    out = {"generated_at": _now(), "day": day, "fx": {"usd_eur": rate, **{k: v for k, v in fx_doc.items() if k != "rate"}},
           "quality": {"status": quality.get("status"),
                       "findings": [dict(f, game=g) for g, v in quality.get("games", {}).items() for f in v.get("findings", [])]},
           "games": {}}
    for gid, g in games.items():
        cat = g["tcgcsv_category"]
        m = load_game(g, day)
        series = load_series(cat)
        est, labels = class_estimates(g)
        sets_scored = score_sets(m) if g.get("score", True) else []
        for s in sets_scored:
            for kind in ("box", "pack", "case"):
                sp = s.get(kind)
                if sp:
                    pts = series.get(sp["product_id"], [])
                    sp["chg7"] = change_pct(pts, day, 7)
                    sp["chg30"] = change_pct(pts, day, 30)
                    sp["spark"] = sparkline(pts, day, 30)
                    sp.pop("prices", None)
        cards = []
        for p in m["products"]:
            if not p.hit_class:
                continue
            pts = series.get(p.product_id, [])
            cards.append({"key": p.key, "set": p.set_code, "number": p.number, "name": p.name, "rarity": p.rarity,
                          "variant": p.variant, "class": p.hit_class, "price": p.price, "price_src": p.price_src,
                          "presale": p.presale, "product_id": p.product_id,
                          "chg7": change_pct(pts, day, 7), "chg30": change_pct(pts, day, 30)})
        other_sets = [{"code": c, **{k: v for k, v in meta.items() if k != "code"},
                       "products": [{"key": p.key, "name": p.name, "kind": p.sealed_kind, "price": p.price, "price_src": p.price_src,
                                     "presale": p.presale, "product_id": p.product_id}
                                    for p in m["products"] if p.set_code == c and p.sealed_kind]}
                      for c, meta in m["sets"].items() if meta["kind"] in ("starter",) ]
        retail = retail_by_set(gid)
        for s in sets_scored:
            s["retail"] = retail.get(s["code"], [])
        for s in other_sets:
            s["retail"] = retail.get(s["code"], [])
        out["games"][gid] = {
            "name": g["name"], "short": g["short"], "category": cat, "score": g.get("score", True),
            "source": {"name": "TCGplayer market data via tcgcsv.com", "last_updated": m["source_last_updated"],
                       "fetched_at": m["fetched_at"], "currency": "USD", "days_of_history": len(store.list_price_days(cat))},
            "estimates": {k: {"label": labels[k], "est": v} for k, v in est.items()},
            "box": g["box"],
            "sets": sets_scored, "cards": cards, "starters": other_sets,
            "all_sets": [{"code": c, "name": v["name"], "kind": v["kind"], "published": v["published"]} for c, v in m["sets"].items()],
            "quality": quality.get("games", {}).get(gid, {}).get("status", "unknown"),
        }
    store.write_json(LATEST, out)
    log.info("published %s (%d games, quality %s)", LATEST, len(out["games"]), out["quality"]["status"])
    return out


SETCODE = re.compile(r"\b(FB|SB|ST|FS)\s?-?(\d{2})\b", re.I)


def retail_by_set(game_id):
    """Group retailer observations (data/state/retail.json) by set code found in the product title/url."""
    if game_id != "dbfw":
        return {}
    st = store.read_json(store.state_path("retail"), {"products": {}})
    by = {}
    for url, p in st["products"].items():
        m = SETCODE.search(p.get("title") or "") or SETCODE.search(url)
        if not m:
            continue
        code = f"{m.group(1).upper()}{m.group(2)}"
        by.setdefault(code, []).append({"retailer": p["retailer"], "title": p["title"], "price": p.get("price"),
                                        "status": p["status"], "url": url, "seen": p["seen"]})
    for code in by:
        by[code].sort(key=lambda x: (x["status"] not in ("in_stock", "preorder"), x["price"] or 9e9))
    return by


def _now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
