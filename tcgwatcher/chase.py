"""Chase Score: expected value of the hit cards in one sealed box.

EV_hits(set) = Σ_class  est[class] × mean(price of cards in class)
ratio = EV_hits / box market price.   '★★' (SAA) is reported separately: one ~€1000+ card you don't pull in 11 of 12 boxes.
Pull-rate estimates are configuration (config/games.yaml), not measurements — the page says so.
"""
from statistics import mean, median
from .model import class_estimates


def score_sets(model):
    game = model["game"]
    est, labels = class_estimates(game)
    overrides = game.get("set_overrides", {})
    by_set = {}
    for p in model["products"]:
        by_set.setdefault(p.set_code, []).append(p)
    out = []
    for code, meta in model["sets"].items():
        if meta["kind"] != "booster" or not game.get("score", True):
            continue
        prods = by_set.get(code, [])
        ov = overrides.get(code, {})
        packs = ov.get("packs", game["box"]["packs"])
        scale = packs / game["box"]["packs"]
        classes = {}
        top = None
        for p in prods:
            if not p.hit_class:
                continue
            c = classes.setdefault(p.hit_class, {"n": 0, "priced": 0, "prices": [], "market_priced": 0})
            c["n"] += 1
            if p.price is not None:
                c["priced"] += 1
                c["prices"].append(p.price)
                c["market_priced"] += p.price_src == "market"
                if top is None or p.price > top.price:
                    top = p
        ev_total = 0.0
        ev_saa = 0.0
        comp = {}
        for cid, c in classes.items():
            e = est.get(cid, 0) * scale
            avg = mean(c["prices"]) if c["prices"] else None
            ev = e * avg if avg is not None else 0.0
            comp[cid] = {"label": labels.get(cid, cid), "n": c["n"], "priced": c["priced"], "market_priced": c["market_priced"],
                         "avg": round(avg, 2) if avg is not None else None,
                         "median": round(median(c["prices"]), 2) if c["prices"] else None,
                         "est": round(e, 3), "ev": round(ev, 2)}
            ev_total += ev
            if cid == "SAA":
                ev_saa += ev
        box = next((p for p in prods if p.sealed_kind == "box"), None)
        pack = next((p for p in prods if p.sealed_kind == "pack"), None)
        case = next((p for p in prods if p.sealed_kind == "case"), None)
        box_price = box.price if box else None
        ev_excl = ev_total - ev_saa
        presale = bool(prods) and all(p.presale for p in prods if p.number)
        n_hits = sum(c["n"] for c in classes.values())
        priced_hits = sum(c["priced"] for c in classes.values())
        flags = list(ov.get("flags", []))
        if presale:
            flags.append("presale")
        if box and box.price_src != "market":
            flags.append("box_listing_price")
        if box and box.prices:
            for sub, pr in box.prices.items():
                if pr.get("marketPrice") and pr.get("lowPrice") and pr["lowPrice"] < 0.3 * pr["marketPrice"]:
                    flags.append("box_spread")
        market_hits = sum(c["market_priced"] for c in classes.values())
        if n_hits and market_hits / n_hits < 0.5:
            flags.append("low_coverage")
        if not n_hits:
            flags.append("no_cards")
        out.append({
            "code": code, "name": meta["name"], "published": meta["published"], "group_id": meta["group_id"],
            "packs": packs, "flags": sorted(set(flags)), "note": ov.get("note"),
            "box": _sealed(box), "pack": _sealed(pack), "case": _sealed(case),
            "composition": comp, "hits_total": n_hits, "hits_priced": priced_hits, "hits_market_priced": market_hits,
            "ev": round(ev_total, 2), "ev_excl_saa": round(ev_excl, 2), "ev_saa": round(ev_saa, 2),
            "ratio": round(ev_total / box_price, 3) if box_price else None,
            "ratio_excl_saa": round(ev_excl / box_price, 3) if box_price else None,
            "top_hit": {"key": top.key, "product_id": top.product_id, "name": top.name, "number": top.number,
                        "class": top.hit_class, "price": top.price, "price_src": top.price_src,
                        "image": top.image, "url": top.url} if top else None,
        })
    out.sort(key=lambda s: (s["ratio_excl_saa"] if s["ratio_excl_saa"] is not None else -1), reverse=True)
    return out


def _sealed(p):
    if not p:
        return None
    return {"key": p.key, "product_id": p.product_id, "name": p.name, "price": p.price, "price_src": p.price_src,
            "prices": p.prices, "presale": p.presale, "image": p.image, "url": p.url}
