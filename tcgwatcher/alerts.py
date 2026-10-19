"""Alerts v3 — only things you can act on now.

  🎯 KOOP        EV€ of hits (excl. ★★) ÷ cheapest EU buy option (CardTrader EU ask or a retailer with stock/pre-order)
                 ≥ buy.threshold. One card per episode; repeats only when the best price drops ≥ buy.rearm_improvement.
  🆕 productlijn new set in the TCGplayer catalogue (= the moment EU pre-orders open), new sealed product.
  🏪 retail      handled in retail.py: only box/case → in stock / pre-order, and only when it is worth buying
                 (ratio ≥ buy.near) or the set is not released yet.
  📋 briefing    weekly (Sunday) by default — `briefing: daily|weekly|off` in config/alerts.yaml.
  ⚠️ quality     only on FAIL (page not updated). Everything else stays in data/ and on the page.
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
MAX_PER_RUN = 8        # safety net: a run never floods the chat; the rest stays in data/alerts/<day>.json


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
    q_mode = cfg.get("quality_issues")
    if q_mode and quality.get("status") == "fail" or (q_mode is True and quality.get("status") == "warn" and fp != state["quality_fp"]):
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
                                   buttons=[[("TCGplayer", f"https://www.tcgplayer.com/search/{ {'dbfw': 'dragon-ball-super-fusion-world', 'dbs': 'dragon-ball-super-ccg'}.get(gid, 'naruto-card-game')}/product?q={grp.get('name','').replace(' ', '%20')}"), ("Radar", SITE_URL)]]))

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
            # 4. buy signal on the EU ratio: EV€ ÷ cheapest EU buy option (see build.best_buy)
            if not gdata.get("alerts", True):
                continue                                   # silent game: page + paper portfolio only
            ranked = [s for s in gdata["sets"] if s.get("ratio_eu") is not None or s.get("best_buy")]
            ranked.sort(key=lambda s: -((s.get("best_buy") or {}).get("ratio") or 0))
            buy = cfg.get("buy", {"threshold": 0.8, "rearm_improvement": 0.1})
            for pos, s in enumerate(ranked, 1):
                key = f"buy:{gid}:{s['code']}"
                bb = s.get("best_buy")
                if not bb or set(s["flags"]) & set(cfg.get("exclude_flags", [])) or "presale" in s["flags"] or s["code"] in muted:
                    state["active"].pop(key, None)
                    continue
                ratio = bb["ratio"] or 0
                if ratio < buy["threshold"]:
                    state["active"].pop(key, None)
                    continue
                ep = state["active"].get(key)
                if ep and not (isinstance(ep, dict) and bb["price"] <= ep["price"] * (1 - buy.get("rearm_improvement", 0.1))):
                    continue        # already alerted this episode and the price did not improve enough
                state["active"][key] = {"since": (ep or {}).get("since", day) if isinstance(ep, dict) else day, "price": bb["price"], "ratio": ratio}
                msgs.append(buy_card(gid, gdata, s, pos, len(ranked), buy, again=bool(ep)))

        # 4b. slabs: the 5 best cards to buy now for graded value — one card per message (photo, where-to-buy comparison,
        #     one button per shop). Episode per card; again when net improves ≥25% or the raw price drops ≥10%.
        buy_min = cfg.get("grading", {}).get("min_profit_eur", 50)
        for gid, gdata in latest["games"].items():
            if not gdata.get("alerts", True):
                continue
            cards = {c["key"]: c for c in gdata.get("cards", [])}
            slabs = {x["key"]: x for x in gdata.get("slab_signals", [])}
            items = []
            for g in gdata.get("grade_signals", []):
                if g["set"] in muted:
                    continue
                c = cards.get(g["key"]) or {}
                opts = []                                   # raw: where is it cheapest, all-in?
                eu = c.get("eu") or {}
                if eu.get("low"):
                    opts.append({"src": "CardTrader" + (" Zero" if eu.get("zero_low") and eu["zero_low"] <= eu["low"] else ""),
                                 "price": round((eu.get("zero_low") or eu["low"]) + (6 if eu.get("zero_low") else 5), 2), "url": eu["url"]})
                re_ = c.get("raw_ebay") or {}
                if re_.get("low"):
                    opts.append({"src": f"eBay ({re_['n']} EU)", "price": re_["low"], "url": re_["url"]})
                cmc = c.get("cm") or {}
                if cmc.get("low"):
                    opts.append({"src": "Cardmarket", "price": round(cmc["low"] + 5, 2),
                                 "url": "https://www.cardmarket.com/en/DragonBallSuper/Products/Search?searchString=" + g["number"]})
                opts.sort(key=lambda o: o["price"])
                if not opts:
                    continue                            # nothing to buy in the EU today → page only (with a Cardmarket search), no message
                raw_price = opts[0]["price"]
                net = round(g["net"] - (raw_price - g["raw_eur"]), 2)
                if net < buy_min:
                    continue
                items.append({"key": g["key"], "g": g, "c": c, "opts": opts, "raw": raw_price, "net": net, "slab": slabs.get(g["key"])})
            items.sort(key=lambda i: -i["net"])
            top_keys = [i["key"] for i in items[:5]]
            for rank, i in enumerate(items[:5], 1):
                key = f"slab:{gid}:{i['key']}"
                ep = state["active"].get(key)
                if ep and not (i["net"] >= ep["net"] * 1.25 or i["raw"] <= ep["raw"] * 0.9):
                    continue
                state["active"][key] = {"since": (ep or {}).get("since", day), "net": i["net"], "raw": i["raw"]}
                g, c, sl = i["g"], i["c"], i["slab"]
                where = "\n".join(f"• €{o['price']:.0f} {o['src']}" for o in i["opts"][:3])
                cls = "Gold (Manga)" if i["key"].endswith(":GOLD") else g["class"].replace("_AA", " AA")
                txt = (f"🏆 <b>SLAB #{rank} · {_card(g['name'], g['number'])} {cls}</b> · {g['set']}\n"
                       f"Raw kopen vanaf <b>€{i['raw']:.0f}</b> → {g['service']} {g['top_grade']} waarde <b>€{g['top_median']:.0f}</b> ({g['top_n']} listings)\n"
                       f"Netto na grading en fees: <b>€{i['net']:.0f}</b> per kaart ({i['net'] / i['raw']:.0%} op inleg)\n\n"
                       f"🛒 <b>Raw, all-in naar NL</b>\n{where}")
                if i["raw"] < 0.5 * g["raw_eur"]:
                    txt += (f"\n⚠️ <b>{1 - i['raw'] / g['raw_eur']:.0%} onder marktwaarde</b> — te mooi? Check feedback, retourbeleid en foto's; "
                            f"betaal alleen met koperbescherming.")
                if sl:
                    txt += f"\n\n🏷 <b>Graded al te koop in EU:</b> {sl['grader']} {sl['grade']:g} €{sl['price']:.0f} ({sl['ratio']:.2f}× raw) — goedkoper dan zelf graden"
                alt = " · ".join(f"{o['service']} €{o['net']:.0f}" for o in g.get("options", [])[1:3])
                txt += (f"\n<i>Andere services: {alt}</i>" if alt else "") + "\n<i>Aanname 40% PSA 10; vraagprijzen als proxy · marktwaarde raw €" + f"{g['raw_eur']:.0f}</i>"
                btn = [[(f"🛒 raw €{o['price']:.0f} · {o['src'].split(' (')[0]}", o["url"])] for o in i["opts"][:2]]
                if sl:
                    btn.append([(f"🏷 graded €{sl['price']:.0f} · {'CardTrader' if 'cardtrader' in sl['url'] else 'eBay'}", sl["url"])])
                btn.append([("📊 Radar", SITE_URL)])
                msgs.append(_m(key, "slab", txt, photo=_big(c.get("image")), buttons=btn))
            for key in [k for k in state["active"] if k.startswith(f"slab:{gid}:")]:
                if key.split(":", 2)[2] not in top_keys:
                    state["active"].pop(key, None)

        # 5. briefing: daily | weekly (Sunday) | off
        mode = cfg.get("briefing", "weekly")
        if mode == "daily" or (mode == "weekly" and dt.date.fromisoformat(day).weekday() == 6):
            msgs.append(briefing(day, latest, state, quality))
        else:
            _remember_ranking(latest, state)

    store.write_json(store.alerts_path(day), {"day": day, "alerts": [{k: v for k, v in m.items() if k != "buttons"} | {"buttons": m.get("buttons")} for m in msgs]}, compact=False)
    store.write_json(store.state_path("alerts"), state, compact=False)
    log.info("%d message(s) for %s (muted: %s)", len(msgs), day, ",".join(sorted(muted)) or "-")
    if len(msgs) > MAX_PER_RUN:
        log.warning("%d messages, capping at %d (rest only in data/alerts)", len(msgs), MAX_PER_RUN)
    if send:
        for m in msgs[:MAX_PER_RUN]:
            telegram.send(m["text"], photo=m.get("photo"), buttons=m.get("buttons"))
    return msgs


def buy_card(gid, gdata, s, pos, n, buy, again=False):
    bb = s["best_buy"]
    comp = s["composition"]
    c = lambda k, l: f"{comp[k]['n']} {l}" if k in comp else None  # noqa: E731
    compo = " · ".join(x for x in (c("SR_AA", "SR-AA"), c("SCR", "SCR"), c("SCR_AA", "★"), c("SAA", "★★")) if x)
    st = {"in_stock": "op voorraad", "preorder": "pre-order"}
    where = "\n".join((f"• €{o['price']:,.2f} {o['source']} ({st.get(o['status'], o['status'])})".replace(",", "X").replace(".", ",").replace("X", ".")
                       + (f" <i>{o['note']}</i>" if o.get("note") else ""))
                      for o in bb.get("options", [])[:3])
    us = f" · US-ratio {s['ratio_excl_saa']:.2f}" if s.get("ratio_excl_saa") is not None else ""
    head = "🎯 <b>KOOP" + (" · goedkoper geworden" if again else "") + f" · {s['code']} {s['name']}</b>"
    txt = (f"{head}\n"
           f"Hits per box <b>€{s['ev_eu_excl_saa']:.0f}</b> / box vanaf <b>€{bb['price']:.0f}</b> → ratio <b>{bb['ratio']:.2f}</b> (drempel {buy['threshold']})\n"
           f"#{pos} van {n}{us}\n\n"
           f"🛒 <b>Waar kopen</b> ({bb.get('offers', '?')} EU-listings)\n{where}\n\n"
           f"Samenstelling: {compo}\n"
           f"Top hit: {_card(s['top_hit']['name'], s['top_hit']['number'])} {s['top_hit']['class'].replace('_AA',' AA')} {_usd(s['top_hit']['price'])}\n"
           f"<i>Waardering TCGplayer-verkopen in € · {s['hits_market_priced']}/{s['hits_total']} hits met marktprijs</i>")
    st_short = {"in_stock": "voorraad", "preorder": "pre-order"}
    btn = [[(f"🛒 {o['source']} €{o['price']:.0f} · {st_short.get(o['status'], o['status'])}", o["url"])] for o in bb.get("options", [])[:3]]
    btn.append([("📊 Radar", SITE_URL)])
    return _m(f"buy:{gid}:{s['code']}", "buy", txt, photo=_big((s.get("box") or {}).get("image") or s["top_hit"].get("image")), buttons=btn)


def _remember_ranking(latest, state):
    g = latest["games"].get("dbfw") or next(iter(latest["games"].values()))
    state["prev_ranking"] = {s["code"]: (s.get("best_buy") or {}).get("ratio") for s in g["sets"] if s.get("best_buy")}


def briefing(day, latest, state, quality):
    g = latest["games"].get("dbfw") or next(iter(latest["games"].values()))
    ranked = sorted((s for s in g["sets"] if s.get("best_buy") and "no_cards" not in s["flags"]),
                    key=lambda s: -(s["best_buy"]["ratio"] or 0))
    prev = state.get("prev_ranking", {})
    lines = []
    for i, s in enumerate(ranked[:6], 1):
        r = s["best_buy"]["ratio"] or 0
        d = r - prev[s["code"]] if prev.get(s["code"]) is not None else None
        arrow = "" if d is None or abs(d) < 0.005 else (f" ↑{d:+.2f}" if d > 0 else f" ↓{d:.2f}")
        flag = " ⚠" if "low_coverage" in s["flags"] else ""
        lines.append(f"{i}. {s['code']} {s['name'][:20]} <code>{r:.2f}</code>{arrow}{flag} · €{s['best_buy']['price']:.0f} {s['best_buy']['source']}")
    _remember_ranking(latest, state)
    movers = sorted((c for c in g["cards"] if c.get("chg7") is not None and c["price"] and c["price"] >= 20 and c["price_src"] == "market" and abs(c["chg7"]) >= 20),
                    key=lambda c: -abs(c["chg7"]))[:5]
    mv = [f"{'▲' if c['chg7']>0 else '▼'} {c['chg7']:+.0f}% {_card(c['name'], c['number'])[:30]} {c['class'].replace('_AA',' AA')} → {_usd(c['price'])}" for c in movers]
    boxes = [f"{s['code']} €{s['best_buy']['price']:.0f} {s['best_buy']['source']}" for s in ranked[:5]]
    retail = [r for s in g["sets"] for r in s.get("retail", []) if r["status"] in ("in_stock", "preorder") and r.get("price")]
    pre = [r for r in retail if r["status"] == "preorder"]; ins = [r for r in retail if r["status"] == "in_stock"]
    rtxt = (f"{len(pre)} pre-order(s) open · {len(ins)} op voorraad" if retail else "nog geen retail-data") if any(s.get("retail") for s in g["sets"]) else "retail-monitor nog niet actief"
    nar = latest["games"].get("naruto", {})
    ntxt = f"{len(nar.get('all_sets', []))} productlijn(en) in catalogus, geen nieuwe" if nar else "—"
    q = quality.get("status", "?")
    nq = sum(len(v.get("findings", [])) for v in quality.get("games", {}).values())
    text = (f"📋 <b>TCGWatcher · {_nl_date(day)}</b>\n"
            f"<i>{len(g['sets'])} sets · {len(g['cards'])} chase cards · data {'✔' if q == 'ok' else '⚠'} ({nq} bekende bronfout(en))</i>\n\n"
            f"<b>Ranking (hits € ÷ goedkoopste EU-box, excl. ★★)</b>\n" + "\n".join(lines) + "\n\n"
            + (f"<b>Bewegingen (7d, ≥20%)</b>\n" + "\n".join(mv) + "\n\n" if mv else "<b>Bewegingen</b> geen kaart ≥20% in 7d\n\n")
            + f"<b>Boxen EU all-in</b> " + " · ".join(boxes) + "\n\n"
            + f"<b>Retail</b> {rtxt}\n<b>Naruto</b> {ntxt}"
            + (f"\n<b>Gemute</b> {', '.join(state['muted'])}" if state.get("muted") else ""))
    return _m(f"briefing:{day}", "briefing", text, buttons=[[("📊 Open radar", SITE_URL)]])


def _m(key, kind, text, photo=None, buttons=None):
    return {"key": key, "kind": kind, "text": text, "photo": photo, "buttons": buttons,
            "at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}


FX = {"rate": 0.9}


def _usd(v):
    """TCGplayer USD shown as EUR (ECB rate of the day); the page keeps both."""
    return "—" if v is None else f"€{v * FX['rate']:,.0f}".replace(",", ".")


def _big(image_url):
    """TCGplayer serves 200w thumbnails in the catalogue; the CDN also has 718×1000 — a slab message deserves the big one."""
    return image_url.replace("_200w.jpg", "_in_1000x1000.jpg") if image_url else None


def _card(name, number=""):
    base = name.split(" - ")[0]
    base = __import__("re").sub(r"\s*\((Super |Bonus Pack )?Alternate Art\)|\s*\(Gold\)", "", base)
    return f"{base} {number}".strip()


def _nl_date(day):
    d = dt.date.fromisoformat(day)
    days = ["maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag", "zondag"]
    months = ["januari", "februari", "maart", "april", "mei", "juni", "juli", "augustus", "september", "oktober", "november", "december"]
    return f"{days[d.weekday()]} {d.day} {months[d.month-1]}"
