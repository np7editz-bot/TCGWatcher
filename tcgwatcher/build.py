"""Build step: scores + trends → site/data/latest.json (what the page reads) with provenance on every block.

Refuses to publish when the quality gate failed (keeps the previous latest.json) — a wrong page is worse than a stale one.
"""
import datetime as dt
import logging
import re
from .config import load_games, load_alerts
from .model import load_game, class_estimates
from .chase import score_sets
from .history import load_series, change_pct, sparkline
from .fx import usd_eur
from .cardtrader import latest_day as ct_latest, eu_prices_by_tcgplayer
from .ebay import latest_day as ebay_latest, slab_signals, grade_signals
from .cardmarket import latest_day as cm_latest
from . import store, SITE, ROOT

log = logging.getLogger("tcgwatcher.build")
LATEST = SITE / "data" / "latest.json"


def build(day, quality=None):
    quality = quality or store.read_json(store.quality_path(day), {"status": "unknown", "games": {}})
    if quality.get("status") == "fail":
        log.error("quality gate failed for %s — not publishing; previous latest.json kept", day)
        return None
    rate, fx_doc = usd_eur(day)
    games = load_games()
    landed_cfg = load_alerts().get("eu_landed", {"zero_fee_eur": 6, "seller_shipping_eur": 15})
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
                          "presale": p.presale, "product_id": p.product_id, "image": p.image, "url": p.url,
                          "chg7": change_pct(pts, day, 7), "chg30": change_pct(pts, day, 30)})
        other_sets = [{"code": c, **{k: v for k, v in meta.items() if k != "code"},
                       "products": [{"key": p.key, "name": p.name, "kind": p.sealed_kind, "price": p.price, "price_src": p.price_src,
                                     "presale": p.presale, "product_id": p.product_id}
                                    for p in m["products"] if p.set_code == c and p.sealed_kind]}
                      for c, meta in m["sets"].items() if meta["kind"] in ("starter",) ]
        ct_doc = ct_latest() if g.get("eu_sources") else None
        eu = eu_prices_by_tcgplayer(ct_doc) if ct_doc else {}
        ct_image = {int(b["tcg_player_id"]): b["image"] for b in ((ct_doc or {}).get("blueprints") or {}).values()
                    if b.get("tcg_player_id") and b.get("image")}
        for c in cards:
            o = eu.get(c["product_id"])
            c["eu"] = {"low": o["low"], "median": o["median"], "n": o["n"], "thin": o["n"] < 5, "zero_low": o.get("zero_low"),
                       "url": f"https://www.cardtrader.com/cards/{o['blueprint_id']}"} if o else None
            # TCGplayer's product photos of the Manga Booster Gold leaders show the non-gold printing (Vegeta, Cell, 2026-10-19);
            # CardTrader scans the actual printing → use that image for Gold variants
            if c["variant"] == "GOLD" and ct_image.get(c["product_id"]):
                c["image"] = ct_image[c["product_id"]]
        for s in sets_scored:
            for kind in ("box", "pack", "case"):
                sp = s.get(kind)
                if sp and eu.get(sp["product_id"]):
                    o = eu[sp["product_id"]]
                    sp["eu"] = {"low": o["low"], "median": o["median"], "n": o["n"], "thin": o["n"] < 5,
                                "zero_low": o.get("zero_low"), "zero_n": o.get("zero_n", 0),
                                "url": f"https://www.cardtrader.com/cards/{o['blueprint_id']}", **landed(o, landed_cfg)}
            # Decision 2026-10-19 (docs/calibration-2026-10-18.md): CardTrader asks sit 15–35% above TCGplayer market and are
            # thin for most hits, so valuation stays TCGplayer (sales-based) converted to EUR; CardTrader provides the EU box ask
            # (denominator) and per-card "what you pay now" info only.
            s["ev_eu_excl_saa"] = round(s["ev_excl_saa"] * rate, 2)
            covered, total = sum(1 for c in cards if c["set"] == s["code"] and c.get("eu")), sum(1 for c in cards if c["set"] == s["code"])
            s["eu_coverage"] = f"{covered}/{total}"
            box_eu = (s.get("box") or {}).get("eu")
            s["ratio_eu"] = round(s["ev_eu_excl_saa"] / box_eu["landed"], 3) if box_eu else None
            if s.get("box") and s["box"].get("price") and box_eu:
                s["spread_eu_us_pct"] = round((box_eu["low"] - s["box"]["price"] * rate) / (s["box"]["price"] * rate) * 100, 1)
        eb = ebay_latest() if g.get("eu_sources") else None
        if eb:
            for c in cards:
                e = eb["cards"].get(c["key"])
                c["slabs"] = {k: {"low": v["low"], "n": v["n"], "url": v["url"], "grader": v["grader"]} for k, v in e["best"].items()} if e else None
                c["raw_ebay"] = (e or {}).get("raw_ebay")
        cm = cm_latest() if g.get("eu_sources") else None
        if cm:
            for c in cards:
                c["cm"] = cm["cards"].get(c["key"])
        retail = retail_by_set(gid, g)
        ebay_boxes = ((eb or {}).get("boxes") or {}) if landed_cfg.get("ebay_boxes", True) else {}   # off: eBay = slabs/raw only
        for s in sets_scored:
            s["retail"] = retail.get(s["code"], [])
            s["ebay_box"] = ebay_boxes.get(s["code"])
            s["cm_box"] = (cm or {}).get("boxes", {}).get(s["code"])
            if s["cm_box"]:
                s["cm_box"]["landed"] = round(s["cm_box"]["low"] + landed_cfg.get("cardmarket_shipping_eur", 18), 2)
            # Valuation (2026-10-21): the EU sources we buy from price every card — Cardmarket sales (avg30 → avg7 → trend),
            # then CardTrader ask × 0.83; TCGplayer × rate only for a card neither has. No set-level switch anymore: the
            # chain is per card. `valuation` tells how much of the set rests on Cardmarket sales; US EV stays as reference.
            s["ev_us_excl_saa"] = s["ev_eu_excl_saa"]
            s["ev_cm_excl_saa"], s["cm_coverage"] = ev_cm(s, cards, rate) if g.get("eu_sources") else (None, "0/0")
            cov_n, cov_t = (int(x) for x in s["cm_coverage"].split("/"))
            if s["ev_cm_excl_saa"] is not None and cov_t:
                s["ev_eu_excl_saa"] = s["ev_cm_excl_saa"]
                s["valuation"] = "cardmarket" if cov_n / cov_t >= 0.5 else "eu-mixed"
            else:
                s["valuation"] = "tcgplayer"
            s["best_buy"] = best_buy(s, landed_cfg.get("min_offers", 3), cardmarket_buy=g.get("cardmarket_box_buy", True))
        for s in other_sets:
            s["retail"] = retail.get(s["code"], [])
        out["games"][gid] = {
            "name": g["name"], "short": g["short"], "category": cat, "score": g.get("score", True),
            "source": {"name": "TCGplayer market data via tcgcsv.com", "last_updated": m["source_last_updated"],
                       "fetched_at": m["fetched_at"], "currency": "USD", "days_of_history": len(store.list_price_days(cat)),
                       "eu": ({"name": "CardTrader marketplace (EN, EUR, cheapest 25 per card)", "day": ct_latest()["day"],
                               "cards_matched": sum(1 for c in cards if c.get("eu"))} if g.get("eu_sources") and ct_latest() else None)},
            "estimates": {k: {"label": labels[k], "est": v} for k, v in est.items()},
            "box": g["box"],
            "sets": sets_scored, "cards": cards, "starters": other_sets,
            "bundles": fixed_content_bundles(m["products"], eu, landed_cfg, rate, (cm or {}).get("bundles") or {}, (cm or {}).get("cards") or {}),
            "slab_signals": [x for x in (slab_signals(eb) if eb else []) if x["key"].startswith(gid + ":")],
            "grade_signals": [x for x in (grade_signals(eb) if eb else []) if x["key"].startswith(gid + ":")],
            "alerts": g.get("alerts", True),
            "slabs_day": eb["day"] if eb else None,
            "all_sets": [{"code": c, "name": v["name"], "kind": v["kind"], "published": v["published"]} for c, v in m["sets"].items()],
            "quality": quality.get("games", {}).get(gid, {}).get("status", "unknown"),
        }
    out["paper"] = paper_summary(out)
    out["news"] = recent_news()
    store.write_json(LATEST, out)
    log.info("published %s (%d games, quality %s)", LATEST, len(out["games"]), out["quality"]["status"])
    return out


SETCODE = re.compile(r"\b(FB|SB|ST|FS|BT|BE|SD|EB)\s?-?(\d{2})\b", re.I)


def eu_ev(s, cards, rate):
    """EV of hits in EUR (excl. SAA): est[class] × mean(EU median, or TCGplayer×rate when no EU offers)."""
    ev, covered, total = 0.0, 0, 0
    for cid, comp in s["composition"].items():
        if cid == "SAA":
            continue
        prices = []
        for c in cards:
            if c["set"] != s["code"] or c["class"] != cid:
                continue
            if c.get("eu"):
                prices.append(c["eu"]["median"]); covered += 1
            elif c["price"] is not None:
                prices.append(c["price"] * rate)
            else:
                continue
            total += 1
        if prices:
            ev += comp["est"] * (sum(prices) / len(prices))
    return round(ev, 2), covered, total


def landed(o, cfg):
    """Listing price → what you pay. Zero listing + fixed fee; otherwise cheapest EU seller + typical shipping."""
    if o.get("zero_low"):
        return {"landed": round(o["zero_low"] + cfg["zero_fee_eur"], 2), "landed_via": "zero", "landed_note": f"Zero €{o['zero_low']:.2f} + €{cfg['zero_fee_eur']} kosten"}
    return {"landed": round(o["low"] + cfg["seller_shipping_eur"], 2), "landed_via": "seller",
            "landed_note": f"€{o['low']:.2f} + ±€{cfg['seller_shipping_eur']} verzending (geen Zero-aanbod)"}


ASK_TO_SALE = 0.83     # CardTrader ask → expected sale price (1 / 1.2, docs/calibration-2026-10-18.md)


BUNDLE_T = re.compile(r"^(?P<label>.+?)( Bundle)?$")


def fixed_content_bundles(products, eu, landed_cfg, fx, cm_bundles=None, cm_cards=None):
    """Sealed products with FIXED contents (anniversary sets, promo packs): no lottery, so value = sum of the cards inside.
    Detected by name: a product without a card number whose name ends in 'Set Bundle' or '… Pack', with cards whose name
    carries '(<that label>)' — e.g. '2nd Anniversary Set Bundle' ↔ 'Kefla - FB08-034 (2nd Anniversary Set)'.
    Page-only (no alerts, no paper positions): shows the bundle price next to the sum of its contents."""
    out = []
    for p in products:
        if p.number or not re.search(r"(Set Bundle|Anniversary Pack|Premium Pack)$", p.name):
            continue
        label = re.sub(r" Bundle$", "", p.name).strip()
        contents = [q for q in products if q.number and f"({label})" in q.name]
        if len(contents) < 2:
            continue
        items, total, priced = [], 0.0, 0
        for q in contents:
            o = eu.get(q.product_id) or {}
            cmr = (cm_cards or {}).get(q.key) or {}
            v = (cmr.get("avg30") or cmr.get("avg7") or cmr.get("trend")
                 or ((o.get("low") or 0) * ASK_TO_SALE or None)
                 or ((q.price * fx) if q.price else None))
            if v:
                total += v; priced += 1
            items.append({"key": q.key, "name": q.name, "number": q.number, "rarity": q.rarity, "price_eur": round(v, 2) if v else None,
                          "image": q.image, "url": q.url, "eu_low": o.get("low")})
        o = eu.get(p.product_id)
        opts = []
        if p.price:
            opts.append({"price": round(p.price * fx, 2), "source": "TCGplayer (US, excl. verzending)", "url": p.url, "ref": True})
        if o and o.get("low"):
            l = landed(o, landed_cfg)
            opts.append({"price": l["landed"], "source": "CardTrader" + (" Zero" if l.get("landed_via") == "zero" else ""),
                         "url": f"https://www.cardtrader.com/cards/{o['blueprint_id']}", "n": o.get("n")})
        cmb = (cm_bundles or {}).get(str(p.product_id))
        if cmb and cmb.get("low"):
            opts.append({"price": round(cmb["low"] + 8, 2), "source": "Cardmarket", "url": cmb["url"], "note": f"laagste aanbod €{cmb['low']:.2f} + ±€8 verzending"})
        opts.sort(key=lambda x: (x.get("ref", False), x["price"]))
        buy = next((x for x in opts if not x.get("ref")), None)
        fixed = p.name.endswith("Set Bundle")       # a 'Pack' holds one or a few of its cards at random: sum ≠ value, no ratio
        out.append({"name": p.name, "product_id": p.product_id, "image": p.image, "url": p.url, "presale": p.presale, "fixed": fixed,
                    "price_usd": p.price, "options": opts, "buy": buy, "sum_eur": round(total, 2), "priced": priced, "n": len(items),
                    "ratio": round(total / buy["price"], 3) if fixed and buy and buy["price"] else None,
                    "contents": sorted(items, key=lambda x: -(x["price_eur"] or 0))})
    return out


def ev_cm(s, cards, rate):
    """EV of the hits on Cardmarket sales (avg30, EUR); per card falls back to avg7 → trend → CardTrader lowest ask × ASK_TO_SALE
    (asks overstate: calibration 2026-10-18 median 1.12–1.39× sales) → TCGplayer × rate only when no EU source prices the card.
    Returns (ev, coverage 'n/total') where n = hits priced on Cardmarket sales."""
    ev, covered, total = 0.0, 0, 0
    for cid, comp in s["composition"].items():
        if comp.get("lottery"):
            continue
        prices = []
        for c in cards:
            if c["set"] != s["code"] or c["class"] != cid:
                continue
            cm = c.get("cm") or {}
            v = cm.get("avg30") or cm.get("avg7")
            if v:
                prices.append(v); covered += 1; total += 1
            elif cm.get("trend"):
                prices.append(cm["trend"]); total += 1
            elif (c.get("eu") or {}).get("low"):
                prices.append(c["eu"]["low"] * ASK_TO_SALE); total += 1
            elif c.get("price") is not None:
                prices.append(c["price"] * rate); total += 1
        if prices:
            ev += comp["est"] * (sum(prices) / len(prices))
    return round(ev, 2), f"{covered}/{total}"


def best_buy(s, min_offers=3, cardmarket_buy=True):
    """Cheapest way to buy this box in Europe right now (what you actually pay), and the EV ratio against it.

    Supply matters: one seller with one box is not a price. `offers` counts listings across CardTrader (EU sellers), eBay EU
    and trusted retailers; below `min_offers` the set is flagged `thin` and never carries a buy signal — the price is shown
    as an indication only.
    """
    opts = []
    box_eu = (s.get("box") or {}).get("eu")
    if box_eu and box_eu.get("landed"):
        opts.append({"price": box_eu["landed"], "source": "CardTrader" + (" Zero" if box_eu.get("landed_via") == "zero" else ""),
                     "url": box_eu["url"], "status": "in_stock", "n": box_eu.get("n") or 1, "note": box_eu.get("landed_note")})
    eb = s.get("ebay_box")
    if eb and eb.get("low"):
        opts.append({"price": eb["low"], "source": f"eBay ({eb['n']} EU)", "url": eb["url"], "status": "in_stock", "n": eb["n"]})
    cmb = s.get("cm_box")
    cm_opt = None
    if cmb and cmb.get("landed"):
        cm_opt = {"price": cmb["landed"], "source": "Cardmarket", "url": cmb["url"], "status": "in_stock", "n": 0,     # guide row ≠ listing count
                  "note": f"laagste aanbod €{cmb['low']:.2f} + ±€18 verzending" + (f" · 30d-verkopen €{cmb['avg30']:.0f}" if cmb.get("avg30") else "")}
        if cardmarket_buy:
            opts.append(cm_opt)
        else:                                   # price guide mixes languages (JP/FR boxes far cheaper): comparison only, never the buy price
            cm_opt["note"] += " · alle talen, indicatie"; cm_opt["indicative"] = True
    for r in s.get("retail", []):
        if r.get("price") and r["status"] in ("in_stock", "preorder") and r.get("kind", "box") == "box" and r["price"] >= 40:
            opts.append({"price": r["price"], "source": r["retailer"], "url": r["url"], "status": r["status"], "n": 1})
    if not opts:
        return None
    opts.sort(key=lambda o: o["price"])
    best = dict(opts[0])
    ev = s.get("ev_eu_excl_saa") or 0
    best["ratio"] = round(ev / best["price"], 3) if best["price"] else None
    best["options"] = sorted(opts[:5] + ([cm_opt] if cm_opt and cm_opt.get("indicative") else []), key=lambda o: o["price"])
    best["offers"] = sum(o.get("n", 1) for o in opts)          # n=0 (price-guide row) adds nothing
    best["thin"] = best["offers"] < min_offers
    if best["thin"] and "thin" not in s["flags"]:
        s["flags"] = sorted(set(s["flags"]) | {"thin"})
    return best


def retail_by_set(game_id, game=None):
    """Group retailer observations (data/state/retail.json) by set code found in the product title/url."""
    if not (game or {}).get("eu_sources"):
        return {}
    st = store.read_json(store.state_path("retail"), {"products": {}})
    by = {}
    for url, p in st["products"].items():
        m = SETCODE.search(p.get("title") or "") or SETCODE.search(url)
        if not m:
            continue
        code = f"{m.group(1).upper()}{m.group(2)}"
        if p.get("product_kind", "box") not in ("box", "case") or not p.get("trusted"):
            continue          # only shops whose availability comes from their own API (Shopify/Woo) reach the page and best_buy
        by.setdefault(code, []).append({"retailer": p["retailer"], "title": p["title"], "price": p.get("price"),
                                        "status": p["status"], "url": url, "seen": p["seen"], "kind": p.get("product_kind", "box")})
    for code in by:
        by[code].sort(key=lambda x: (x["status"] not in ("in_stock", "preorder"), x["price"] or 9e9))
    return by


def paper_summary(latest):
    """Open paper positions valued with today's data (same yardstick as the purchase) + totals per kind."""
    from .paper import _load, value_now
    from .ebay import latest_day as ebay_latest
    eb = ebay_latest()
    pos = [p for p in _load()["positions"] if p["status"] == "open"]
    rows, totals = [], {}
    for p in pos:
        v, ev_now = value_now(p, latest, eb)
        rows.append({**p, "value": v, "pnl": (round(v - p["price"], 2) if v is not None else None), "ev_now": ev_now})
        t = totals.setdefault(p["kind"], {"n": 0, "cost": 0.0, "value": 0.0, "valued": 0, "wins": 0})
        t["n"] += 1
        if v is not None:                          # cost only over valued positions, so % is like-for-like
            t["cost"] += p["price"]; t["value"] += v; t["valued"] += 1; t["wins"] += v > p["price"]
    for t in totals.values():
        t["cost"] = round(t["cost"], 2); t["value"] = round(t["value"], 2)
    reports = sorted(p.name for p in (ROOT / "docs").glob("paper-????-??.md")) if (ROOT / "docs").exists() else []
    return {"positions": rows, "totals": totals, "last_report": reports[-1] if reports else None}


def recent_news(n=12):
    st = store.read_json(store.state_path("news"), {"seen": {}})
    items = [{"source": k.split("|")[0], "url": k.split("|", 1)[1], **v} for k, v in st["seen"].items()]
    items.sort(key=lambda i: (i.get("date") or "", i.get("seen") or ""), reverse=True)
    return items[:n]


def _now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
