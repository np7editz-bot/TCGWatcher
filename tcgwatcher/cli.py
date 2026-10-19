"""python -m tcgwatcher <fetch|validate|build|alerts|all> [--day YYYY-MM-DD] [--force] [--no-send]"""
import argparse
import datetime as dt
import logging
import sys
from . import store


def main(argv=None):
    ap = argparse.ArgumentParser(prog="tcgwatcher")
    ap.add_argument("step", choices=["fetch", "validate", "build", "alerts", "retail", "news", "cardtrader", "ebay", "calibrate", "paper", "all"])
    ap.add_argument("--day", help="source day to process (default: last synced day)")
    ap.add_argument("--force", action="store_true", help="fetch even if the source did not change")
    ap.add_argument("--no-send", action="store_true", help="do not send Telegram messages")
    ap.add_argument("--report", action="store_true", help="paper: write the monthly review now")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    log = logging.getLogger("tcgwatcher")

    day = a.day
    if a.step in ("fetch", "all"):
        from .fetch import fetch_all
        r = fetch_all(force=a.force)
        day = day or r["day"]
        log.info("fetch: %s", r)
        if r["skipped"] and a.step == "all":
            log.info("source unchanged; validate/build/alerts still run for %s", day)
    if a.step in ("cardtrader", "all"):
        from .cardtrader import fetch_day
        try:
            d = fetch_day()
            log.info("cardtrader: %s", f"{len(d['offers'])} offers, {d['api_calls']} calls" if d else "skipped")
        except Exception as e:  # noqa: BLE001
            log.error("cardtrader failed: %s", e)
    if a.step in ("ebay", "all"):
        from .ebay import fetch_day as ebay_fetch
        try:
            d = ebay_fetch()
            log.info("ebay: %s", f"{len(d['cards'])} cards, {d['api_calls']} calls" if d else "skipped")
        except Exception as e:  # noqa: BLE001
            log.error("ebay failed: %s", e)
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
            open_positions(day, latest, alerts)
            if a.step == "all" and dt.date.today().day == 1:
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
