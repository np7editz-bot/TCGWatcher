"""Quality gate. Runs after fetch, before build. Writes data/quality/<day>.json.

status: ok | warn | fail
- fail  → build keeps the previous latest.json and alerts send a 'data issue' message. Nothing wrong gets published.
- warn  → published, but the page shows the warnings and the report lists them.
Checks are deliberately simple and explainable; every finding carries the ids needed to look it up at the source.
"""
from collections import Counter
from statistics import median
from .config import load_games
from .model import load_game
from . import store


def validate(day):
    games = load_games()
    report = {"day": day, "status": "ok", "games": {}}
    for gid, g in games.items():
        findings = []
        cat = g["tcgcsv_category"]
        if not store.prices_path(cat, day).exists():
            if not store.list_price_days(cat):          # game just added, never fetched: pending, not a failure
                report["games"][gid] = {"status": "pending", "findings": [_f("info", "no_data_yet", f"category {cat} not fetched yet")]}
                continue
            findings.append(_f("fail", "no_price_file", f"no price file for category {cat} on {day}"))
            report["games"][gid] = {"status": "fail", "findings": findings}
            continue
        m = load_game(g, day)
        prods = m["products"]
        if not prods:
            findings.append(_f("fail" if g.get("score", True) else "warn", "empty_catalog", "catalog has no products"))
        # 1. schema basics
        bad = [p.product_id for p in prods if not p.name or p.product_id is None]
        if bad:
            findings.append(_f("fail", "missing_fields", f"{len(bad)} products without name/id", ids=bad[:20]))
        # 2. duplicate canonical keys (source catalog errors such as two products carrying the same Number+variant)
        card_kinds = {c for c, meta in m["sets"].items() if meta["kind"] in ("booster", "starter")}
        keys = Counter(p.key for p in prods if p.number and p.set_code in card_kinds)
        dups = {k: n for k, n in keys.items() if n > 1}
        if dups:
            findings.append(_f("warn", "duplicate_keys", f"{len(dups)} canonical keys occur more than once",
                               ids=sorted(dups)[:30]))
        # 3. number/name mismatch: '... - FB10-067' in name but Number says FB10-068
        mism = []
        for p in prods:
            if p.number and " - " in p.name and p.set_code in card_kinds:
                tail = p.name.split(" - ")[-1].split(" (")[0].strip()
                if tail.startswith(p.number.split("-")[0] + "-") and tail != p.number:
                    mism.append(f"{p.product_id}:{p.name}!={p.number}")
        if mism:
            findings.append(_f("warn", "number_name_mismatch", f"{len(mism)} products where the number in the name differs from the Number field", ids=mism[:30]))
        # 4. price sanity: outliers vs class median, and absurd sealed listings
        by_class = {}
        for p in prods:
            if p.hit_class and p.price is not None:
                by_class.setdefault((p.set_code, p.hit_class), []).append(p)
        outl = []
        for (s, c), lst in by_class.items():
            if len(lst) >= 4:
                med = median(x.price for x in lst)
                for x in lst:
                    if med > 0 and x.price > 25 * med and x.price_src != "market":
                        outl.append(f"{x.product_id}:{x.name}:{x.price}:{x.price_src}")
        if outl:
            findings.append(_f("warn", "price_outlier_listing", f"{len(outl)} non-market prices >25× the class median", ids=outl[:30]))
        sealed_odd = [f"{p.product_id}:{p.name}:{p.price}" for p in prods if p.sealed_kind and p.price and p.price > 50000]
        if sealed_odd:
            findings.append(_f("warn", "sealed_price_absurd", "sealed product priced > $50,000", ids=sealed_odd))
        # 5. coverage of market prices on hit cards (released booster sets only)
        for code, meta in m["sets"].items():
            if meta["kind"] != "booster":
                continue
            hits = [p for p in prods if p.set_code == code and p.hit_class]
            if not hits:
                continue
            if all(p.presale for p in hits):
                continue
            cov = sum(1 for p in hits if p.price_src == "market") / len(hits)
            if cov < 0.5:
                findings.append(_f("warn", "low_market_coverage", f"{code}: only {cov:.0%} of {len(hits)} hit cards have a market price"))
        # 6. day-over-day: a set that lost >20% of its products or price rows vs the previous day → fail (source glitch)
        days = store.list_price_days(cat)
        prev = [d for d in days if d < day]
        if prev:
            pm = load_game(g, prev[-1])
            prev_counts = Counter(p.set_code for p in pm["products"] if p.price is not None)
            cur_counts = Counter(p.set_code for p in prods if p.price is not None)
            for code, n in prev_counts.items():
                if n >= 20 and cur_counts.get(code, 0) < 0.8 * n:
                    findings.append(_f("fail", "priced_rows_dropped", f"{code}: priced products fell from {n} to {cur_counts.get(code, 0)}"))
        status = "ok"
        if any(f["level"] == "fail" for f in findings):
            status = "fail"
        elif findings:
            status = "warn"
        report["games"][gid] = {"status": status, "findings": findings,
                                "products": len(prods), "priced": sum(1 for p in prods if p.price is not None),
                                "market_priced": sum(1 for p in prods if p.price_src == "market")}
    levels = [v["status"] for v in report["games"].values()]
    report["status"] = "fail" if "fail" in levels else ("warn" if "warn" in levels else "ok")
    store.write_json(store.quality_path(day), report, compact=False)
    return report


def _f(level, code, msg, ids=None):
    f = {"level": level, "code": code, "message": msg}
    if ids:
        f["ids"] = ids
    return f
