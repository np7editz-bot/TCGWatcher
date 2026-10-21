"""Paper portfolio: every signal becomes a fictional purchase, so the signals can be checked against reality.

Positions (data/paper/positions.json, append-only):
  box   — 🎯 KOOP: one box at the all-in EU price of the signal; tracked as resale value (what a box sells for) AND EV then/now.
  raw   — 📈 grade-kans top 5: the RAW card bought at TCGplayer market (€); tracked as raw value. No grading assumed.
  slab  — 🏆 slab-koop top 5: the graded card at the EU listing price; tracked as cheapest EU listing of the same grade.
  control — every scored set NOT on a buy signal, one box at its all-in price, opened once per month (cohort): if the
            control group performs as well as the signals, the ratio predicts nothing and thresholds must change.

Monthly review (1st of the month, or `python -m tcgwatcher paper --report`): docs/paper-<YYYY-MM>.md + one Telegram message:
P&L per position type, hit rate, control vs signals, EV-then vs EV-now per box, and what to adjust.
"""
import datetime as dt
import logging
from . import store, ROOT
from .build import LATEST

log = logging.getLogger("tcgwatcher.paper")
PATH = store.DATA / "paper" / "positions.json"
SITE_URL = "https://np7editz-bot.github.io/TCGWatcher/"


def _load():
    return store.read_json(PATH, {"positions": []})


def _save(doc):
    store.write_json(PATH, doc, compact=False)


def open_positions(day, latest, msgs, active_buy=()):
    """Called after alerts: turn today's signals into positions (idempotent per key+day) and open the monthly control cohort.
    `active_buy` = set codes whose KOOP episode is still active (alerted earlier) — they count as held, never as control."""
    doc = _load()
    live = [p for p in doc["positions"] if p["status"] != "void"]       # a voided position does not block a clean re-open
    have = {(p["kind"], p["key"], p["opened"]) for p in live}
    have_keys = {(p["kind"], p["key"]) for p in live if p["status"] == "open"}
    from .config import load_alerts
    cfg = load_alerts()
    thr, excl = cfg.get("buy", {}).get("threshold", 0.8), set(cfg.get("exclude_flags", []))
    added = []

    def add(pos):
        if (pos["kind"], pos["key"], day) in have or (pos["kind"], pos["key"]) in have_keys:
            return
        pos.update({"opened": day, "status": "open"})
        doc["positions"].append(pos); added.append(pos)

    for gid, g in latest["games"].items():
      if not g.get("sets"):
        continue
      sets = {s["code"]: s for s in g.get("sets", [])}
      # the KOOP rule itself (not the message): silent games build evidence too
      buy_codes = {code for code, s in sets.items() if s.get("best_buy") and (s["best_buy"].get("ratio") or 0) >= thr
                   and not (set(s["flags"]) & excl) and "presale" not in s["flags"]} | {c for c in active_buy if c in sets}
      for code in sorted(buy_codes):
        s = sets.get(code)
        if s and s.get("best_buy"):
            add({"kind": "box", "game": gid, "key": f"buy:{gid}:{code}", "code": code, "name": s["name"], "price": s["best_buy"]["price"],
                 "source": s["best_buy"]["source"], "ev_then": s["ev_eu_excl_saa"], "ratio_then": s["best_buy"]["ratio"],
                 "valuation": s.get("valuation", "tcgplayer")})
      cards = {c["key"]: c for c in g.get("cards", [])}
      for sg in g.get("grade_signals", [])[:5]:
        c = cards.get(sg["key"]) or {}
        eu = c.get("eu") or {}
        cmr = c.get("cm") or {}
        if eu.get("low"):
            price, src = round(eu["low"] + 5, 2), "CardTrader EU laagste ask + €5 verzending"
        elif cmr.get("avg30"):
            price, src = round(cmr["avg30"], 2), "Cardmarket 30d-verkopen"
        elif c.get("price"):
            price, src = round(c["price"] * latest["fx"]["usd_eur"], 2), "TCGplayer market (raw)"
        else:
            continue
        add({"kind": "raw", "game": gid, "key": sg["key"], "code": sg["set"], "name": f"{sg['name']} {sg['number']} {sg['class']}",
             "price": price, "source": src, "tcg_then": sg["raw_eur"], "psa10_then": sg["top_median"], "net_then": sg["net"]})
      for x in g.get("slab_signals", [])[:5]:
        add({"kind": "slab", "game": gid, "key": f"{x['key']}:{x['grade']}", "code": x["set"], "name": f"{x['name']} {x['number']} {x['grader']} {x['grade']:g}",
             "price": x["price"], "source": "eBay EU", "grade": x["grade"], "raw_then": x["raw_eur"], "card_key": x["key"]})
      # control cohort: once per month per game, every scored set without a buy signal
      month = day[:7]
      if not any(p["kind"] == "control" and p["status"] == "open" and p["opened"][:7] == month and p.get("game", "dbfw") == gid for p in doc["positions"]):
        buy_codes |= {p["code"] for p in doc["positions"] if p["kind"] == "box" and p["status"] == "open" and p.get("game", "dbfw") == gid}
        for code, s in sets.items():
            # the control group must be the same universe the KOOP rule picks from: sets it could have chosen but did not
            if code in buy_codes or not s.get("best_buy") or (set(s["flags"]) & excl) or "presale" in s["flags"] or s["best_buy"].get("thin"):
                continue
            add({"kind": "control", "game": gid, "key": f"control:{gid}:{code}:{month}", "code": code, "name": s["name"], "price": s["best_buy"]["price"],
                 "source": s["best_buy"]["source"], "ev_then": s["ev_eu_excl_saa"], "ratio_then": s["best_buy"]["ratio"],
                 "valuation": s.get("valuation", "tcgplayer")})
    if added:
        _save(doc)
        log.info("paper: %d new position(s): %s", len(added), ", ".join(f"{p['kind']}:{p['code']}" for p in added))
    return added


def box_price_by_source(s, source):
    """Today's all-in price of a box from the SAME source as the purchase (a source switch is not a market move)."""
    src = (source or "").lower()
    if src.startswith("cardtrader"):
        return ((s.get("box") or {}).get("eu") or {}).get("landed")
    if src.startswith("cardmarket"):
        return (s.get("cm_box") or {}).get("landed")
    if src.startswith("ebay"):
        return (s.get("ebay_box") or {}).get("low")
    for r in s.get("retail", []):                   # a retailer: its own current price, in stock or pre-order
        if r.get("retailer") == source and r.get("price") and r.get("status") in ("in_stock", "preorder") and r.get("kind", "box") == "box":
            return r["price"]
    return None


def value_now(pos, latest, ebay_doc):
    """Current value of a position in €, with the same yardstick as the purchase (same source, same valuation)."""
    g = latest["games"].get(pos.get("game", "dbfw"), {})
    if pos["kind"] in ("box", "control"):
        s = next((s for s in g.get("sets", []) if s["code"] == pos["code"]), None)
        if not s:
            return None, None
        val = pos.get("valuation") or "tcgplayer"
        eu_vals = ("cardmarket", "eu-mixed")
        ev_now = s.get("ev_eu_excl_saa") if (val in eu_vals and s.get("valuation") in eu_vals) else (
            s.get("ev_us_excl_saa") if val == "tcgplayer" else None)
        return box_price_by_source(s, pos.get("source")), ev_now
    if pos["kind"] == "raw":
        c = next((c for c in g.get("cards", []) if c["key"] == pos["key"]), None)
        if not c:
            return None, None
        src = pos.get("source", "")
        if src.startswith("CardTrader"):
            return (round(c["eu"]["low"] + 5, 2) if (c.get("eu") or {}).get("low") else None), None
        if src.startswith("Cardmarket"):
            return (round(c["cm"]["avg30"], 2) if (c.get("cm") or {}).get("avg30") else None), None
        return (round(c["price"] * latest["fx"]["usd_eur"], 2) if c.get("price") else None), None
    if pos["kind"] == "slab":
        c = (ebay_doc or {}).get("cards", {}).get(pos["card_key"])
        b = (c or {}).get("best", {}).get(str(float(pos["grade"])))
        return ((b or {}).get("eu_low") or (b or {}).get("low")), None
    return None, None


def report(day, latest=None, send=True):
    from .ebay import latest_day as ebay_latest
    latest = latest or store.read_json(LATEST)
    eb = ebay_latest()
    doc = _load()
    pos = [p for p in doc["positions"] if p["status"] == "open"]
    if not pos:
        log.info("paper: no open positions")
        return None
    rows, tot = [], {}
    for p in pos:
        v, ev_now = value_now(p, latest, eb)
        pnl = (v - p["price"]) if v is not None else None
        t = tot.setdefault(p["kind"], {"cost": 0.0, "value": 0.0, "n": 0, "wins": 0, "unknown": 0})
        t["n"] += 1
        if v is None:
            t["unknown"] += 1                       # no price from the purchase source today: out of cost AND value
        else:
            t["cost"] += p["price"]; t["value"] += v; t["wins"] += pnl > 0
        rows.append((p, v, pnl, ev_now))
    lines = [f"# Paper-portfolio · stand {day}", "",
             "Elke melding is een fictieve aankoop; dit rapport vergelijkt de waarde nu met de aankoopprijs (zelfde maatstaf).",
             "Boxen: prijs nu bij DEZELFDE bron als de aankoop (een bronwissel is geen marktbeweging) + EV toen vs nu op dezelfde waardering. "
             "Raw: zelfde maatstaf als de aankoop (CardTrader ask+5 / Cardmarket 30d / TCGplayer). Slabs: goedkoopste EU-listing zelfde grade. "
             "Posities zonder prijs bij hun bron vandaag tellen niet mee in kosten én waarde.", "",
             "| soort | posities | kostprijs | waarde nu | P&L | winnaars |", "|---|---|---|---|---|---|"]
    tg = [f"📒 <b>Paper-portfolio · {day}</b>"]
    label = {"box": "🎯 boxen (koop)", "control": "⚪ controlegroep", "raw": "📈 raw kaarten", "slab": "🏆 slabs"}
    for k in ("box", "control", "raw", "slab"):
        t = tot.get(k)
        if not t:
            continue
        known = t["n"] - t["unknown"]
        pnl = t["value"] - t["cost"]
        pct = pnl / t["cost"] * 100 if t["cost"] else 0
        unk = f" · {t['unknown']} zonder prijs" if t["unknown"] else ""
        lines.append(f"| {label[k]} | {known}/{t['n']} | €{t['cost']:.0f} | €{t['value']:.0f} | {pnl:+.0f} ({pct:+.1f}%) | {t['wins']}/{known} |")
        tg.append(f"{label[k]}: {known} pos · €{t['cost']:.0f} → €{t['value']:.0f} · <b>{pct:+.1f}%</b> · {t['wins']}/{known} winnaars{unk}")
    box_pct = next((float(l.split("(")[-1].split("%")[0]) for l in lines if "boxen" in l), None)
    ctl_pct = next((float(l.split("(")[-1].split("%")[0]) for l in lines if "controlegroep" in l), None)
    verdict = ""
    if box_pct is not None and ctl_pct is not None:
        verdict = (f"Koopsignalen {box_pct:+.1f}% vs controlegroep {ctl_pct:+.1f}% → "
                   + ("de ratio voegt iets toe." if box_pct > ctl_pct + 2 else "geen aantoonbaar voordeel; drempel/EV-schatting herzien."))
        lines += ["", f"**Oordeel:** {verdict}"]; tg.append(verdict)
    lines += ["", "## Posities", "", "| soort | set/kaart | geopend | koop € | bron | nu € | P&L | EV toen → nu |", "|---|---|---|---|---|---|---|---|"]
    for p, v, pnl, ev_now in sorted(rows, key=lambda r: (r[0]["kind"], r[0]["opened"])):
        ev = f"€{p['ev_then']:.0f} → €{ev_now:.0f}" if p.get("ev_then") is not None and ev_now is not None else "—"
        lines.append(f"| {p['kind']} | {p['code']} {p['name'][:40]} | {p['opened']} | {p['price']:.0f} | {p['source']} | {v if v is None else f'{v:.0f}'} | {'—' if pnl is None else f'{pnl:+.0f}'} | {ev} |")
    lines += ["", "## Wat bijstellen", "", "- Boxen die onder aankoop staan: was de EU-prijs te hoog ingeschat (verzending) of daalde de markt (aanbod na release)?",
              "- EV toen → nu: zakt de EV structureel na de eerste weken, dan is de koopdrempel bij jonge sets te laag.",
              "- Raw kaarten: stijgers zijn kandidaten voor een echte test-inzending (gem-rate meten)."]
    md = "\n".join(lines)
    path = ROOT / "docs" / f"paper-{day[:7]}.md"
    path.write_text(md, encoding="utf-8")
    if send:
        from . import telegram
        telegram.send("\n".join(tg), buttons=[[("📄 Rapport", f"https://github.com/np7editz-bot/TCGWatcher/blob/main/docs/paper-{day[:7]}.md"), ("📊 Radar", SITE_URL)]])
    log.info("paper report: %s (%d positions)", path, len(pos))
    return md
