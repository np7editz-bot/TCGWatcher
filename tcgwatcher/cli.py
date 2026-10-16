"""python -m tcgwatcher <fetch|validate|build|alerts|all> [--day YYYY-MM-DD] [--force] [--no-send]"""
import argparse
import logging
import sys
from . import store


def main(argv=None):
    ap = argparse.ArgumentParser(prog="tcgwatcher")
    ap.add_argument("step", choices=["fetch", "validate", "build", "alerts", "all"])
    ap.add_argument("--day", help="source day to process (default: last synced day)")
    ap.add_argument("--force", action="store_true", help="fetch even if the source did not change")
    ap.add_argument("--no-send", action="store_true", help="do not send Telegram messages")
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
    if a.step in ("alerts", "all"):
        from .alerts import run
        alerts = run(day, quality, send=not a.no_send)
        for al in alerts:
            log.info("alert %s", al["key"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
