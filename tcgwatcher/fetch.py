"""Fetch step: pull the day's data from tcgcsv.com and store it raw.

Rules that make the data trustworthy:
- Source data is stored as returned (products and price rows), with the source's own timestamp.
- The catalog file of a group is rewritten only when its content changed (git history keeps every version).
- A price day file is written once; re-running on the same source day is a no-op unless --force.
- Nothing downstream ever edits these files.
"""
import datetime as dt
import logging
from .config import load_games
from .tcgcsv import TcgCsv
from . import store

log = logging.getLogger("tcgwatcher.fetch")


def source_day(last_updated: str) -> str:
    """'2026-10-15T20:06:01+0000' → '2026-10-15' (the source's publication day, used as the data day)."""
    return last_updated[:10]


def fetch_all(client: TcgCsv | None = None, force: bool = False) -> dict:
    client = client or TcgCsv()
    games = load_games()
    last_updated = client.last_updated()
    day = source_day(last_updated)
    sync = store.read_json(store.state_path("sync"), {})
    result = {"day": day, "last_updated": last_updated, "games": {}, "skipped": False}

    if sync.get("last_updated") == last_updated and not force:
        log.info("source unchanged since %s — nothing to fetch", last_updated)
        result["skipped"] = True
        return result

    for gid, g in games.items():
        cat = g["tcgcsv_category"]
        groups = client.groups(cat)
        store.write_json(store.groups_path(cat), {"fetched_at": _now(), "source_last_updated": last_updated,
                                                  "category": cat, "results": groups})
        price_rows = []
        changed_catalogs = 0
        for grp in groups:
            grp_id = grp["groupId"]
            products = client.products(cat, grp_id)
            cat_obj = {"category": cat, "groupId": grp_id, "group": grp, "results": products}
            old = store.read_json(store.catalog_path(cat, grp_id))
            if old is None or store.digest(old.get("results")) != store.digest(products):
                cat_obj["fetched_at"] = _now()
                cat_obj["source_last_updated"] = last_updated
                store.write_json(store.catalog_path(cat, grp_id), cat_obj)
                changed_catalogs += 1
            for row in client.prices(cat, grp_id):
                row = dict(row)
                row["groupId"] = grp_id
                price_rows.append(row)
        pfile = store.prices_path(cat, day)
        if pfile.exists() and not force:
            log.warning("price file for %s/%s exists; not overwriting", cat, day)
        else:
            store.write_json(pfile, {"category": cat, "day": day, "source_last_updated": last_updated,
                                     "fetched_at": _now(), "results": price_rows})
        result["games"][gid] = {"groups": len(groups), "price_rows": len(price_rows),
                                "changed_catalogs": changed_catalogs}
        log.info("%s: %d groups, %d price rows, %d catalogs changed", gid, len(groups), len(price_rows), changed_catalogs)

    sync.pop("note", None)
    sync.update({"last_updated": last_updated, "day": day, "fetched_at": _now(), "requests": client.requests_made})
    store.write_json(store.state_path("sync"), sync, compact=False)
    return result


def _now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
