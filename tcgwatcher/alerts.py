"""Alerts v2.

Two kinds of messages:
  signal cards  — one message per event, with photo and buttons: buy signal (ratio crossing), new product line
                  (pre-order signal), new sealed product, data-quality change. Retail transitions are sent by retail.py.
  daily briefing — one message: ranking with shift vs yesterday, movers, boxes, retail, Naruto, data status.
Muting: send `/mute FB10` (or `/mute all`, `/unmute FB10`, `/status`) to the bot; read at the start of each run.
State (data/state/alerts.json): active episodes, seen groups/sealed, muted sets, previous ranking, quality fingerprint.
"""
import datetime as dt
import logging
from .config import load_alerts, load_games
from . import store, telegram
from .build import LATEST

log = logging.getLogger("tcgwatcher.alerts")
SITE_URL = "https://np7editz-bot.github.io/TCGWatcher/"


def run(day, quality=None, send=True):
    cfg = load_alerts()
    games = load_games()
    latest = store.read_json(LATEST)
    if latest:
        FX["rate"] = latest.get("fx", {}).get("usd_eur", 0.9)
    quality = quality or store.read_json(store.quality_path(day), {})
    state = store.read_json(store.state_path("alerts"), {})
    for k, v in {"active": {}, "seen_groups": {}, "seen_sealed": {}, "muted": [], "prev_ranking": {}, "quality_fp": ""}.items():
        state.setdefault(k, v)
    msgs = []   # each: {"key","kind","text","photo","buttons"}

    # 0. bot commands
    for cmd, arg in (telegram.commands() if send else []):
        if cmd == "mute" and arg:
            state["muted"] = sorted(set(state["muted"]) | {arg})
        elif cmd == "unmute" and arg:
            state["muted"] = [m for m in state["muted"] if m != arg] if arg != "ALL" else []
        elif cmd == "status":
            msgs.append(_m(f"status:{day}", "status", f"🤖 TCGWatcher draait. Gemute sets: {', '.join(state['muted']) or 'geen'}. Laatste data-dag: {day}."))
    muted = set(state["muted"])

    # 1. data quality: only when the set of findings changes, or on fail
    fp = "|".join(sorted(f"{g}:{f['code']}:{','.join(f.get('ids', [])[:5])}" for g, v in quality.get("games", {}).items() for f in v.get("findings", [])))
    if cfg.get("quality_issues") and quality.get("status") in ("fail", "warn") and (fp != state["quality_fp"] or quality["status"] == "fail"):
        findings = [f"{g}: {f['code']}" for g, v in quality.get("games", {}).items() for f in v.get("findings", [])]
        lvl = "FAIL — pagina niet bijgewerkt" if quality["status"] == "fail" else "gewijzigd"
        msgs.append(_m(f"quality:{day}", "quality", f"⚠️ <b>Datakwaliteit {lvl}</b>\n" + "\n".join("• " + x for x in findings[:8])
                       + f"\n<i>Bekende TCGplayer-catalogusfouten; details in data/quality/{day}.json</i>",
                       buttons=[[("Rapport", f"https://github.com/np7editz-bot/TCGWatcher/blob/main/data/quality/{day}.json")]]))
    state["quality_fp"] = fp

    # 2. new product lines (earliest pre-order signal)
    for gid, g in games.items():
        cat = g["tcgcsv_category"]
        groups = store.read_json(store.groups_path(cat), {"results": []})["results"]
        seen = state["seen_groups"].setdefault(str(cat), {})
        first_run = not seen
        for grp in groups:
            k = str(grp["groupId"])
            if k not in seen:
                seen[k] = {"abbr": grp.get("abbreviation"), "name": grp.get("name"), "published": (grp.get("publishedOn") or "")[:10], "seen": day}
                if cfg.get("new_group") and not first_run:
                    msgs.append(_m(f"new_group:{cat}:{k}", "new_group",
                                   f"🆕 <b>{g['short']} · nieuwe productlijn</b>\n<b>{grp.get('name')}</b> ({grp.get('abbreviation') or '—'})\n"
                                   f"Release {(grp.get('publishedOn') or '')[:10]} · nu in de TCGplayer-catalogus — pre-orders bij EU-shops openen meestal rond dit moment.",
                                   buttons=[[("TCGplayer", f"https://www.tcgplayer.com/search/{'dragon-ball-super-fusion-world' if gid=='dbfw' else 'naruto-card-game'}/product?q={grp.get('name','').replace(' ', '%20')}"), ("Radar", SITE_URL)]]))

    if latest:
        for gid, gdata in latest["games"].items():
            # 3. new sealed products
            seen_s = state["seen_sealed"].setdefault(gid, {})
            first = not seen_s
            for s in gdata["sets"] + gdata.get("starters", []):
                sealed = [s[k] for k in ("box", "case", "pack") if s.get(k)] if "box" in s else s.get("products", [])
                for sp in sealed:
                    k = str(sp["product_id"])
                    if k not in seen_s:
                        seen_s[k] = {"name": sp["name"], "seen": day}
                        if cfg.get("new_sealed_product") and not first:
                            msgs.append(_m(f"new_sealed:{gid}:{k}", "new_sealed",
                                           f"📦 <b>{gdata['short']} · nieuw sealed product</b>\n<b>{sp['name']}</b>"
                                           + (f"\n{_usd(sp['price'])} ({sp['price_src']})" if sp.get("price") else "") + ("\n🟡 presale" if sp.get("presale") else ""),
                                           photo=sp.get("image"), buttons=[[("TCGplayer", sp.get("url") or SITE_URL), ("Radar", SITE_URL)]]))
            # 4. buy signal: ratio crossing (episode), with context
            ranked = [s for s in gdata["sets"] if s["ratio_excl_saa"] is not None]
            for pos, s in enumerate(ranked, 1):
                key = f"ratio:{gid}:{s['code']}"
                if set(s["flags"]) & set(cfg.get("exclude_flags", [])) or "presale" in s["flags"] or s["code"] in muted:
                    state["active"].pop(key, None)
                    continue
                ratio = s["ratio"] if cfg["ratio"].get("include_saa") else s["ratio_excl_saa"]
                if ratio >= cfg["ratio"]["threshold"]:
                    if key not in state["active"]:
                        state["active"][key] = day
                        comp = s["composition"]
                        c = lambda k, l: f"{comp[k]['n']} {l}" if k in comp else None  # noqa: E731
                        compo = " · ".join(x for x in (c("SR_AA", "SR-AA"), c("SCR", "SCR"), c("SCR_AA", "★"), c("SAA", "★★")) if x)
                        best = next((r for r in s.get("retail", []) if r.get("price") and r["status"] in ("in_stock", "preorder")), None)
                        txt = (f"🎯 <b>KOOPSIGNAAL · {s['code']} {s['name']}</b>\n"
                               f"Ratio <b>{ratio:.2f}</b> (drempel {cfg['ratio']['threshold']}) · #{pos} van {len(ranked)}\n"
                               f"Hits {_usd(s['ev_excl_saa'])} / box {_usd(s['box']['price'])} (TCGplayer, in €)\n"
                               f"Top hit: {_card(s['top_hit']['name'], s['top_hit']['number'])} {s['top_hit']['class'].replace('_AA',' AA')} {_usd(s['top_hit']['price'])}\n"
                               f"Samenstelling: {compo}\n"
                               f"Marktprijzen: {s['hits_market_priced']}/{s['hits_total']} hits")
                        if best:
                            txt += f"\n🛒 EU: €{best['price']:.2f} bij {best['retailer']} ({'pre-order' if best['status']=='preorder' else 'op voorraad'})"
                        btn = [[("📊 Radar", SITE_URL), ("TCGplayer", s["box"].get("url") or SITE_URL)]]
                        if best:
                            btn.append([(f"🛒 {best['retailer']}", best["url"])])
                        msgs.append(_m(key, "ratio", txt, photo=(s["box"] or {}).get("image") or s["top_hit"].get("image"), buttons=btn))
                else:
                    state["active"].pop(key, None)

        # 5. daily briefing (always, once per day)
        msgs.append(briefing(day, latest, state, quality))

    store.write_json(store.alerts_path(day), {"day": day, "alerts": [{k: v for k, v in m.items() if k != "buttons"} | {"buttons": m.get("buttons")} for m in msgs]}, compact=False)
    store.write_json(store.state_path("alerts"), state, compact=False)
    log.info("%d message(s) for %s (muted: %s)", len(msgs), day, ",".join(sorted(muted)) or "-")
    if send:
        for m in msgs:
            telegram.send(m["text"], photo=m.get("photo"), buttons=m.get("buttons"))
    return msgs


def briefing(day, latest, state, quality):
    g = latest["games"].get("dbfw") or next(iter(latest["games"].values()))
    ranked = [s for s in g["sets"] if s["ratio_excl_saa"] is not None and "no_cards" not in s["flags"]]
    prev = state.get("prev_ranking", {})
    lines = []
    for i, s in enumerate(ranked[:6], 1):
        r = s["ratio_excl_saa"]
        d = r - prev[s["code"]] if s["code"] in prev else None
        arrow = "" if d is None or abs(d) < 0.005 else (f" ↑{d:+.2f}" if d > 0 else f" ↓{d:.2f}")
        flag = " ⚠" if "low_coverage" in s["flags"] else ""
        lines.append(f"{i}. {s['code']} {s['name'][:22]} <code>{r:.2f}</code>{arrow}{flag}")
    state["prev_ranking"] = {s["code"]: s["ratio_excl_saa"] for s in ranked}
    movers = sorted((c for c in g["cards"] if c.get("chg7") is not None and c["price"] and c["price"] >= 20 and c["price_src"] == "market" and abs(c["chg7"]) >= 20),
                    key=lambda c: -abs(c["chg7"]))[:5]
    mv = [f"{'▲' if c['chg7']>0 else '▼'} {c['chg7']:+.0f}% {_card(c['name'], c['number'])[:30]} {c['class'].replace('_AA',' AA')} → {_usd(c['price'])}" for c in movers]
    boxes = [f"{s['code']} {_usd(s['box']['price'])}" + (f" ({s['box']['chg7']:+.1f}%)" if s['box'].get('chg7') is not None else "") for s in ranked[:5] if s.get("box")]
    retail = [r for s in g["sets"] for r in s.get("retail", []) if r["status"] in ("in_stock", "preorder") and r.get("price")]
    pre = [r for r in retail if r["status"] == "preorder"]; ins = [r for r in retail if r["status"] == "in_stock"]
    rtxt = (f"{len(pre)} pre-order(s) open · {len(ins)} op voorraad" if retail else "nog geen retail-data") if any(s.get("retail") for s in g["sets"]) else "retail-monitor nog niet actief"
    nar = latest["games"].get("naruto", {})
    ntxt = f"{len(nar.get('all_sets', []))} productlijn(en) in catalogus, geen nieuwe" if nar else "—"
    top = ranked[0] if ranked else None
    q = quality.get("status", "?")
    nq = sum(len(v.get("findings", [])) for v in quality.get("games", {}).values())
    text = (f"📋 <b>TCGWatcher · {_nl_date(day)}</b>\n"
            f"<i>{len(g['sets'])} sets · {len(g['cards'])} chase cards · data {'✔' if q == 'ok' else '⚠'} ({nq} bekende bronfout(en))</i>\n\n"
            f"<b>Ranking (EV-ratio, excl. ★★)</b>\n" + "\n".join(lines) + "\n\n"
            + (f"<b>Bewegingen (7d, ≥20%)</b>\n" + "\n".join(mv) + "\n\n" if mv else "<b>Bewegingen</b> geen kaart ≥20% in 7d\n\n")
            + f"<b>Boxen</b> " + " · ".join(boxes) + "\n\n"
            + f"<b>Retail</b> {rtxt}\n<b>Naruto</b> {ntxt}"
            + (f"\n<b>Gemute</b> {', '.join(state['muted'])}" if state.get("muted") else ""))
    photo = (top["top_hit"].get("image") if top and top.get("top_hit") else None)
    return _m(f"briefing:{day}", "briefing", text, photo=photo, buttons=[[("📊 Open radar", SITE_URL)]])


def _m(key, kind, text, photo=None, buttons=None):
    return {"key": key, "kind": kind, "text": text, "photo": photo, "buttons": buttons,
            "at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}


FX = {"rate": 0.9}


def _usd(v):
    """TCGplayer USD shown as EUR (ECB rate of the day); the page keeps both."""
    return "—" if v is None else f"€{v * FX['rate']:,.0f}".replace(",", ".")


def _card(name, number=""):
    base = name.split(" - ")[0]
    base = __import__("re").sub(r"\s*\((Super |Bonus Pack )?Alternate Art\)|\s*\(Gold\)", "", base)
    return f"{base} {number}".strip()


def _nl_date(day):
    d = dt.date.fromisoformat(day)
    days = ["maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag", "zondag"]
    months = ["januari", "februari", "maart", "april", "mei", "juni", "juli", "augustus", "september", "oktober", "november", "december"]
    return f"{days[d.weekday()]} {d.day} {months[d.month-1]}"
