"""python -m tcgwatcher <fetch|validate|build|alerts|all> [--day YYYY-MM-DD] [--force] [--no-send]"""
import argparse
import datetime as dt
import logging
import sys
from . import store


def main(argv=None):
    ap = argparse.ArgumentParser(prog="tcgwatcher")
    ap.add_argument("step", choices=["fetch", "validate", "build", "alerts", "retail", "news", "cardtrader", "cardmarket", "ebay", "calibrate", "paper", "probe", "all"])
    ap.add_argument("--category", type=int, help="probe: TCGplayer category id")
    ap.add_argument("--slug", help="probe: output name")
    ap.add_argument("--cm-name", help="probe: name to look for in Cardmarket's catalogues")
    ap.add_argument("--ct-name", help="probe: name to look for in CardTrader's game list")
    ap.add_argument("--news", nargs="*", default=[], help="probe: news page URLs to sample")
    ap.add_argument("--day", help="source day to process (default: last synced day)")
    ap.add_argument("--force", action="store_true", help="fetch even if the source did not change")
    ap.add_argument("--no-send", action="store_true", help="do not send Telegram messages")
    ap.add_argument("--report", action="store_true", help="paper: write the monthly review now")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    log = logging.getLogger("tcgwatcher")

    day = a.day
    if a.step == "probe":
        from .probe import run as probe
        probe(a.category, a.slug, a.cm_name, a.news, a.ct_name)
        return 0
    if a.step in ("fetch", "all"):
        from .fetch import fetch_all
        r = fetch_all(force=a.force)
        day = day or r["day"]
        log.info("fetch: %s", r)
        if r["skipped"] and a.step == "all":
            log.info("source unchanged; validate/build/alerts still run for %s", day)
    day = day or store.read_json(store.state_path("sync"), {}).get("day")
    if not day:
        log.error("no day known; run fetch first or pass --day")
        return 2
    quality = None
    if a.step in ("validate", "all"):
        from .validate import validate
        quality = validate(day)
        log.info("validate: %s", quality["status"])
    if a.step in ("build", "all"):
        from .build import build
        out = build(day, quality)
        log.info("build: %s", "published" if out else "NOT published (quality fail)")
    # EU sources join against today's catalogue (keys, product ids, sets), so they run AFTER the first build; a second build
    # then folds them in. Outside `all` each step is still callable on its own.
    if a.step in ("cardtrader", "all"):
        from .cardtrader import fetch_day
        try:
            d = fetch_day()
            log.info("cardtrader: %s", f"{len(d['offers'])} offers, {d['api_calls']} calls" if d else "skipped")
        except Exception as e:  # noqa: BLE001
            log.error("cardtrader failed: %s", e)
    if a.step in ("cardmarket", "all"):
        from .cardmarket import fetch_day as cm_fetch
        try:
            d = cm_fetch()
            log.info("cardmarket: %s", f"{len(d['cards'])} cards, {len(d['boxes'])} boxes" if d else "skipped")
        except Exception as e:  # noqa: BLE001
            log.error("cardmarket failed: %s", e)
    if a.step in ("ebay", "all"):
        from .ebay import fetch_day as ebay_fetch
        try:
            d = ebay_fetch()
            log.info("ebay: %s", f"{len(d['cards'])} cards, {d['api_calls']} calls" if d else "skipped")
        except Exception as e:  # noqa: BLE001
            log.error("ebay failed: %s", e)
    if a.step == "all":
        from .build import build
        out = build(day, quality)
        log.info("build (with EU sources): %s", "published" if out else "NOT published (quality fail)")
    if a.step in ("calibrate",):
        from .calibrate import run as cal
        out = cal(day)
        log.info("calibration: %s", out or "no CardTrader data")
    if a.step in ("alerts", "all"):
        from .alerts import run
        alerts = run(day, quality, send=not a.no_send)
        for al in alerts:
            log.info("alert %s", al["key"])
        from .paper import open_positions, report as paper_report
        from .build import LATEST
        latest = store.read_json(LATEST)
        if latest:
            st = store.read_json(store.state_path("alerts"), {})
            active = st.get("active", {})
            latest["sent_slabs"] = st.get("sent_slabs", {})      # page: mark slab candidates already sent to Telegram
            store.write_json(LATEST, latest)
            if open_positions(day, latest, alerts, active_buy={k.split(":")[-1] for k in active if k.startswith("buy:")}):
                from .build import paper_summary       # new positions → refresh the page's portfolio block right away
                latest["paper"] = paper_summary(latest)
                store.write_json(LATEST, latest)
            # monthly review: the first successful `all` run of a month without a report yet (a failed run on the 1st must not
            # cost a month), and only once there are positions from an earlier month to review (first report: 1 November)
            if a.step == "all":
                from . import ROOT
                from .paper import _load
                month = dt.date.today().strftime("%Y-%m")
                older = any(p["status"] == "open" and p["opened"][:7] < month for p in _load()["positions"])
                if older and not (ROOT / "docs" / f"paper-{month}.md").exists():
                    paper_report(day, latest, send=not a.no_send)
    if a.step == "paper":
        from .paper import report as paper_report
        from .build import LATEST
        paper_report(day or dt.date.today().isoformat(), store.read_json(LATEST), send=not a.no_send)
    if a.step in ("news", "all"):
        from .news import run as news_run
        n = news_run(send=not a.no_send)
        log.info("news: %d items, %d message(s), errors=%s", n["items"], len(n["messages"]), n["errors"])
    if a.step in ("retail", "all"):
        from .retail import run as retail_run, format_alerts
        from . import telegram
        first = not store.read_json(store.state_path("retail"), {}).get("products")
        muted = store.read_json(store.state_path("alerts"), {}).get("muted", [])
        r = retail_run()
        log.info("retail: %d rows, errors=%s, %d transitions", r["rows"], r["errors"], len(r["transitions"]))
        from .config import load_alerts
        from .build import LATEST
        msgs = format_alerts(r["transitions"], first, muted, latest=store.read_json(LATEST, {}), buy=load_alerts().get("buy"))
        store.write_json(store.DATA / "retail" / f"{r['day']}.alerts.json",
                         {"day": r["day"], "messages": msgs, "transitions": r["transitions"]}, compact=False)
        if not a.no_send:
            for m in msgs[:20]:
                telegram.send(m["text"], buttons=m.get("buttons"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
