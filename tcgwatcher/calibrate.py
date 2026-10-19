"""Calibration: how far do CardTrader EU asks sit from TCGplayer market prices? Writes docs/calibration-<day>.md.
Ratio = CardTrader lowest EN/EUR ask ÷ (TCGplayer market USD × ECB rate). Per hit class: median ratio, spread, n.
Thin markets (<5 offers) are reported separately. This is the evidence for which EU source to trust for valuation."""
from statistics import median, quantiles
from .config import load_games
from .model import load_game
from .cardtrader import latest_day, eu_prices_by_tcgplayer
from .fx import usd_eur
from . import store, ROOT


def run(day):
    doc = latest_day()
    if not doc:
        return None
    eu = eu_prices_by_tcgplayer(doc)
    rate, _ = usd_eur(day)
    m = load_game(load_games()["dbfw"], day)
    rows = []
    for p in m["products"]:
        o = eu.get(p.product_id)
        if not o or p.price is None or p.price_src != "market" or not (p.hit_class or p.sealed_kind):
            continue
        us = p.price * rate
        rows.append({"set": p.set_code, "cls": p.hit_class or f"sealed:{p.sealed_kind}", "name": p.name, "us_eur": round(us, 2),
                     "ct_low": o["low"], "ct_median": o["median"], "n": o["n"], "ratio_low": round(o["low"] / us, 3) if us else None,
                     "ratio_med": round(o["median"] / us, 3) if us else None})
    by = {}
    for r in rows:
        by.setdefault(r["cls"], []).append(r)
    lines = [f"# Kalibratie CardTrader vs TCGplayer — {day}", "",
             f"Koers {rate} · {len(rows)} producten met zowel een TCGplayer-marktprijs als EN/EUR-aanbod op CardTrader (dag {doc['day']}).",
             "Ratio = CardTrader laagste aanbod ÷ TCGplayer market (in €). 1,00 = gelijk; <1 = EU goedkoper.", "",
             "| Klasse | n | ratio laagste (mediaan) | P25–P75 | ratio mediaan-aanbod | dun (<5 aanb.) |", "|---|---|---|---|---|---|"]
    for cls, lst in sorted(by.items(), key=lambda kv: -len(kv[1])):
        rl = sorted(x["ratio_low"] for x in lst if x["ratio_low"])
        rm = sorted(x["ratio_med"] for x in lst if x["ratio_med"])
        q = quantiles(rl, n=4) if len(rl) >= 4 else [rl[0], rl[0], rl[-1]]
        lines.append(f"| {cls} | {len(lst)} | {median(rl):.2f} | {q[0]:.2f}–{q[2]:.2f} | {median(rm):.2f} | {sum(1 for x in lst if x['n'] < 5)} |")
    lines += ["", "## Grootste afwijkingen (laagste aanbod vs TCGplayer)", "", "| Set | Kaart | TCGplayer € | CT laagste | CT mediaan | n | ratio |", "|---|---|---|---|---|---|---|"]
    for r in sorted(rows, key=lambda r: abs((r["ratio_low"] or 1) - 1), reverse=True)[:25]:
        lines.append(f"| {r['set']} | {r['name'][:45]} | {r['us_eur']:.2f} | {r['ct_low']:.2f} | {r['ct_median']:.2f} | {r['n']} | {r['ratio_low']:.2f} |")
    out = ROOT / "docs" / f"calibration-{day}.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    store.write_json(store.DATA / "cardtrader" / f"calibration-{day}.json", {"day": day, "rate": rate, "rows": rows}, compact=False)
    return out
