"""Alert rules over latest.json + source state. Every alert has a stable key; data/state/alerts.json remembers
active episodes so you get one message when something starts, not one per day.
"""
import datetime as dt
import logging
from .config import load_alerts, load_games
from . import store, telegram
from .build import LATEST

log = logging.getLogger("tcgwatcher.alerts")


def run(day, quality=None, send=True):
    cfg = load_alerts()
    games = load_games()
    latest = store.read_json(LATEST)
    quality = quality or store.read_json(store.quality_path(day), {})
    state = store.read_json(store.state_path("alerts"), {"active": {}, "seen_groups": {}, "seen_sealed": {}})
    alerts = []

    # 0. data quality
    if cfg.get("quality_issues") and quality.get("status") in ("fail", "warn"):
        n = sum(len(v.get("findings", [])) for v in quality.get("games", {}).values())
        lvl = "FAIL" if quality["status"] == "fail" else "warn"
        alerts.append(_a(f"quality:{day}", "quality", f"⚠️ Data quality {lvl}: {n} finding(s) on {day}. "
                         + ("Page NOT updated." if lvl == "FAIL" else "Page updated with warnings.")))

    # 1. new groups (sets / product lines) per game — the earliest pre-order signal
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
                    alerts.append(_a(f"new_group:{cat}:{k}", "new_group",
                                     f"🆕 <b>{g['short']}</b>: nieuwe productlijn in TCGplayer-catalogus: <b>{grp.get('name')}</b> "
                                     f"({grp.get('abbreviation')}), release {(grp.get('publishedOn') or '')[:10]}. "
                                     f"Pre-orders openen meestal rond dit moment."))
        if first_run:
            log.info("%s: seeded %d known groups (no alerts on first run)", gid, len(seen))

    if latest:
        for gid, gdata in latest["games"].items():
            # 2. new sealed products
            seen_s = state["seen_sealed"].setdefault(gid, {})
            first = not seen_s
            for s in gdata["sets"] + gdata.get("starters", []):
                sealed = [s[k] for k in ("box", "case", "pack") if s.get(k)] if "box" in s else s.get("products", [])
                for sp in sealed:
                    k = str(sp["product_id"])
                    if k not in seen_s:
                        seen_s[k] = {"name": sp["name"], "seen": day}
                        if cfg.get("new_sealed_product") and not first:
                            alerts.append(_a(f"new_sealed:{gid}:{k}", "new_sealed",
                                             f"📦 <b>{gdata['short']}</b>: nieuw sealed product gelist: <b>{sp['name']}</b>"
                                             + (f" — {_usd(sp['price'])} ({sp['price_src']})" if sp.get("price") else "")
                                             + (" · presale" if sp.get("presale") else "")))
            # 3. ratio and box price moves (booster sets with a score)
            for s in gdata["sets"]:
                if set(s["flags"]) & set(cfg.get("exclude_flags", [])) or "presale" in s["flags"]:
                    state["active"].pop(f"ratio:{gid}:{s['code']}", None)   # episode ends while excluded
                    continue
                ratio = s["ratio"] if cfg["ratio"].get("include_saa") else s["ratio_excl_saa"]
                key = f"ratio:{gid}:{s['code']}"
                if ratio is not None and ratio >= cfg["ratio"]["threshold"]:
                    if key not in state["active"]:
                        state["active"][key] = day
                        alerts.append(_a(key, "ratio", f"🎯 <b>{s['code']} {s['name']}</b>: EV-ratio {ratio:.2f} ≥ {cfg['ratio']['threshold']} "
                                         f"(hits {_usd(s['ev_excl_saa'])} / box {_usd(s['box']['price'])}). Top hit: {s['top_hit']['name']} {_usd(s['top_hit']['price'])}."))
                else:
                    state["active"].pop(key, None)
                box = s.get("box") or {}
                c7 = box.get("chg7")
                if c7 is not None:
                    for lbl, thr, emoji in (("drop", cfg["box_price"]["drop_pct_7d"], "📉"), ("rise", cfg["box_price"]["rise_pct_7d"], "📈")):
                        key = f"box_{lbl}:{gid}:{s['code']}"
                        hit = c7 <= thr if lbl == "drop" else c7 >= thr
                        if hit and key not in state["active"]:
                            state["active"][key] = day
                            alerts.append(_a(key, f"box_{lbl}", f"{emoji} <b>{s['code']} {s['name']}</b> box {_usd(box['price'])}: {c7:+.1f}% vs 7-daags gemiddelde."))
                        elif not hit:
                            state["active"].pop(key, None)
            # 4. single chase card moves
            cc = cfg["card_price"]
            for c in gdata["cards"]:
                if c.get("chg7") is None or c["price"] is None or c["price"] < cc["min_price_usd"] or c["price_src"] != "market":
                    continue
                if abs(c["chg7"]) >= cc["move_pct_7d"]:
                    key = f"card:{c['key']}:{'up' if c['chg7'] > 0 else 'down'}"
                    if key not in state["active"]:
                        state["active"][key] = day
                        alerts.append(_a(key, "card_move", f"{'🔺' if c['chg7'] > 0 else '🔻'} {c['set']} {c['name']} ({c['class']}): {_usd(c['price'])}, {c['chg7']:+.0f}% in 7d."))
                else:
                    state["active"].pop(f"card:{c['key']}:up", None)
                    state["active"].pop(f"card:{c['key']}:down", None)

    store.write_json(store.alerts_path(day), {"day": day, "alerts": alerts}, compact=False)
    store.write_json(store.state_path("alerts"), state, compact=False)
    log.info("%d alert(s) for %s", len(alerts), day)
    if alerts and send:
        head = f"<b>TCGWatcher · {day}</b>\n"
        body = "\n\n".join(a["text"] for a in alerts)
        telegram.send(head + body)
    return alerts


def _a(key, kind, text):
    return {"key": key, "kind": kind, "text": text, "at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}


def _usd(v):
    return "—" if v is None else f"${v:,.0f}"
