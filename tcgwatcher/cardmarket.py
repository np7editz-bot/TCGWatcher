"""Cardmarket price guide (official, public, daily) — the EU sales-based price next to CardTrader's asks.

Cardmarket publishes, for every game, three JSON files on its CDN (no key, no bot-wall):
  productCatalog/productList/products_singles_<game>.json     idProduct, name, idExpansion, idMetacard
  productCatalog/productList/products_nonsingles_<game>.json  sealed: boxes, packs, decks, cases
  productCatalog/priceGuide/price_guide_<game>.json           per idProduct: low (cheapest offer), trend, avg1/avg7/avg30
                                                              (average SALE price over 1/7/30 days), *-foil variants
Game 13 = Dragon Ball Super (incl. Fusion World). Cards are joined through CardTrader's blueprint `card_market_ids`;
sealed boxes through the product name. Only the rows we use are kept per day (data/cardmarket/<day>.json, immutable).
"""
import datetime as dt
import logging
import re
import requests
from . import store, USER_AGENT

log = logging.getLogger("tcgwatcher.cardmarket")
CDN = "https://downloads.s3.cardmarket.com/productCatalog"
GAME_IDS = {"dbfw": 13, "dbs": 13}
SCHEMA = 7            # bump when join() adds fields: an older day file is then re-joined (the guide is daily anyway)
NOT_EN = re.compile(r"(asia|non-english|japanese|\bjp\b|korean|chinese)", re.I)


def fetch_files(game_id=13, session=None):
    s = session or requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    out = {}
    for key, path in (("singles", f"productList/products_singles_{game_id}.json"),
                      ("nonsingles", f"productList/products_nonsingles_{game_id}.json"),
                      ("prices", f"priceGuide/price_guide_{game_id}.json")):
        r = s.get(f"{CDN}/{path}", timeout=120)
        r.raise_for_status()
        out[key] = r.json()
    return out


def slug(name):
    """Cardmarket URL slug: apostrophes, dots and slashes dropped, every other non-alphanumeric run becomes '-'
    ("Saiyan's Pride" → Saiyans-Pride, "(V.3 - God Rare)" → V3-God-Rare, "Son Goten/Trunks : Youth" → Son-GotenTrunks-Youth)."""
    return re.sub(r"[^A-Za-z0-9]+", "-", re.sub(r"['./]", "", name)).strip("-")


def box_url(name):
    """Sealed product page: /Products/Booster-Boxes/<Name-Slug> (name 'Awakened Pulse Booster Box [Fusion World]' →
    Awakened-Pulse-Booster-Box-Fusion-World)."""
    return "https://www.cardmarket.com/en/DragonBallSuper/Products/Booster-Boxes/" + slug(name)


# A catalogue name with several printings gets "(V.n - <label>)" on EVERY printing, V1 included; the label is Cardmarket's
# rarity of that printing: the card's own rarity for the base printing, "Alternate Art" / "God Rare" for parallels.
# Verified in the browser (2026-10-19): ...-V1-Secret-Rare, ...-V2-Alternate-Art, ...-V3-God-Rare, ...-V2-Special-Rare.
VERSION_LABEL = {"AA": "Alternate Art", "SAA": "God Rare", "BPAA": "Alternate Art", "GOLD": "Alternate Art", "GDR": "God Rare",
                 "SPR": "Special Rare", "SLR": "Special Leader Rare", "SCR": "Secret Rare"}
# Cardmarket's English Fusion World expansions: the early sets carry no "[Fusion World]" suffix (verified: FB03 'Raging Roar',
# FB06 'Rivals Clash'), later ones do (verified: FB07, FB08, FB10, SB01). Each set also has a '… Non-English' expansion, which the
# join avoids by taking the expansion the English booster box sits in.
EXPANSION_OVERRIDE = {"FB01": "Awakened Pulse", "FB02": "Blazing Aura", "FB03": "Raging Roar", "FB04": "Ultra Limit",
                      "FB05": "New Adventure", "FB06": "Rivals Clash"}


def expansion_name(gid, code, set_name, box_name=None):
    """Cardmarket expansion name. Fusion World: '<Set> [Fusion World]' (from the box name when we have it).
    Masters: Cardmarket uses Bandai's series numbering, not the subtitle — BT18–BT25 are 'Zenkai Series Set 01'…'08'
    (verified: BT21 → Zenkai-Series-Set-04); later sets keep their name until verified."""
    if code in EXPANSION_OVERRIDE:
        return EXPANSION_OVERRIDE[code]
    if box_name:
        return box_name.replace(" Booster Box", "")
    m = re.fullmatch(r"BT(\d{2})", code or "")
    if gid == "dbs" and m and 18 <= int(m.group(1)) <= 25:
        return f"Zenkai Series Set {int(m.group(1)) - 17:02d}"
    return (set_name or "") + (" [Fusion World]" if gid == "dbfw" else "")


def card_url(expansion_name, product_name, version=1, variant="", rarity="", printings=1):
    """Single page: /Products/Singles/<Expansion-Slug>/<Product-Slug>[-V<n>-<Label>].
    Cardmarket keeps one product per printing under the same catalogue name; the 2nd, 3rd … printing gets a
    "(V.n - Label)" suffix in its URL, e.g. Saiyans-Pride-Fusion-World/Son-Goten-FB08-121-Fusion-World-V3-God-Rare
    (verified in the browser 2026-10-19). n = rank of idProduct among the same name in the same expansion; label from our
    variant code (VERSION_LABEL)."""
    label = VERSION_LABEL.get(variant) or rarity
    tail = f" (V.{version} - {label})" if printings > 1 and label else ""
    return f"https://www.cardmarket.com/en/DragonBallSuper/Products/Singles/{slug(expansion_name)}/{slug(product_name + tail)}"


def _f(row, field):
    """Price-guide value with the foil fallback: Cardmarket keeps `low/trend/avg*` for the non-foil version and
    `*-foil` for the foil one; Fusion World hits are foil-only, so their sales live in the foil fields (0/None elsewhere)."""
    v = row.get(field)
    return v if v else row.get(f"{field}-foil")


def join(files, latest, ct_doc):
    """Keep only what we use: price-guide rows for our chase cards (via CardTrader card_market_ids) and sealed boxes."""
    prices = {p["idProduct"]: p for p in files["prices"].get("priceGuides", [])}
    cm_ids_by_tcg = {}
    for b in ((ct_doc or {}).get("blueprints") or {}).values():
        if b.get("tcg_player_id") and b.get("card_market_ids"):
            cm_ids_by_tcg[int(b["tcg_player_id"])] = [int(x) for x in b["card_market_ids"] if x]
    singles = files["singles"].get("products", [])
    names = {p["idProduct"]: p.get("name") or "" for p in singles}
    siblings = {}                                   # (idExpansion, name) → sorted idProducts: position = Cardmarket "V.n"
    for p in singles:
        siblings.setdefault((p.get("idExpansion"), p.get("name")), []).append(p["idProduct"])
    version, printings = {}, {}
    for ids in siblings.values():
        for n, i in enumerate(sorted(ids), 1):
            version[i] = n; printings[i] = len(ids)
    exp_of = {p["idProduct"]: p.get("idExpansion") for p in singles}
    cards, boxes = {}, {}
    for gid, g in latest["games"].items():
        sealed = [p for p in files["nonsingles"].get("products", []) if "Booster Box" in p.get("name", "")]
        for s in g.get("sets", []):
            name = (s.get("name") or "").lower()
            if not name:
                continue
            hits = [p for p in sealed if name in p["name"].lower() and "case" not in p["name"].lower() and not NOT_EN.search(p["name"])]
            if gid == "dbfw":
                hits = [p for p in hits if "fusion world" in p["name"].lower()] or hits
            else:
                hits = [p for p in hits if "fusion world" not in p["name"].lower()]
            rows = [(p, prices.get(p["idProduct"])) for p in hits]
            rows = [(p, r) for p, r in rows if r and r.get("low")]
            if rows:
                p, r = min(rows, key=lambda x: x[1]["low"])
                boxes[s["code"]] = {"idProduct": p["idProduct"], "idExpansion": p.get("idExpansion"), "name": p["name"], "low": r["low"], "trend": r.get("trend"),
                                    "avg7": r.get("avg7"), "avg30": r.get("avg30"),
                                    "url": box_url(p["name"])}
        # expansion name per set = box name without "Booster Box" ("Saiyan's Pride Booster Box [Fusion World]" → "Saiyan's Pride [Fusion World]"),
        # else the set name (+ " [Fusion World]" for Fusion World)
        exp_name = {s["code"]: expansion_name(gid, s["code"], s.get("name"), (boxes.get(s["code"]) or {}).get("name") if gid == "dbfw" else None)
                    for s in g.get("sets", [])}
        # Cardmarket lists some printings twice (same name, two expansions, e.g. FB03-139 as 783017 and 790367).
        # Per set: the expansion the booster box sits in, else the expansion that covers most of the set's cards (tie → lowest id).
        set_exp = {}
        for code, b in boxes.items():
            if b.get("idExpansion"):
                set_exp[code] = b["idExpansion"]
        counts = {}
        for c in g.get("cards", []):
            code = c["key"].split(":")[1]
            for i in cm_ids_by_tcg.get(c.get("product_id")) or []:
                if i in prices and exp_of.get(i):
                    counts.setdefault(code, {}).setdefault(exp_of[i], set()).add(c["key"])
        for code, by_exp in counts.items():
            set_exp.setdefault(code, min(by_exp, key=lambda e: (-len(by_exp[e]), e)))
        for c in g.get("cards", []):
            ids = cm_ids_by_tcg.get(c.get("product_id"))
            if not ids:
                continue
            rows = [prices[i] for i in ids if i in prices]
            if not rows:
                continue
            code, variant = c["key"].split(":")[1], c["key"].split(":")[3]
            in_exp = [r for r in rows if exp_of.get(r["idProduct"]) == set_exp.get(code)]
            r = min(in_exp or rows, key=lambda x: _f(x, "low") or 9e9)
            pid = r["idProduct"]
            cards[c["key"]] = {"idProduct": pid, "name": names.get(pid), "version": version.get(pid, 1),
                               "low": _f(r, "low"), "trend": _f(r, "trend"),
                               "avg1": _f(r, "avg1"), "avg7": _f(r, "avg7"), "avg30": _f(r, "avg30"),
                               "foil": not r.get("avg30") and bool(r.get("avg30-foil")),
                               "url": card_url(exp_name.get(code, ""), names[pid], version.get(pid, 1), variant, c.get("rarity") or "",
                                               printings.get(pid, 1)) if names.get(pid) else None}
    return cards, boxes


def fetch_day(latest=None, ct_doc=None, day=None, session=None):
    day = day or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    path = store.DATA / "cardmarket" / f"{day}.json"
    if path.exists():
        old = store.read_json(path)
        if old.get("schema", 1) >= SCHEMA:
            log.info("cardmarket day file exists for %s; not refetching", day)
            return old
        log.info("cardmarket day file for %s has schema %s < %s; re-joining", day, old.get("schema", 1), SCHEMA)
    from .build import LATEST
    from .cardtrader import latest_day as ct_latest
    latest = latest or store.read_json(LATEST)
    ct_doc = ct_doc if ct_doc is not None else ct_latest()
    files = fetch_files(13, session)
    cards, boxes = join(files, latest, ct_doc)
    doc = {"day": day, "schema": SCHEMA, "fetched_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "game_id": 13,
           "guide_created_at": files["prices"].get("createdAt"), "cards": cards, "boxes": boxes,
           "note": "low = cheapest offer; avg30 = average sale price last 30 days (EUR). Source: Cardmarket public price guide."}
    store.write_json(path, doc)
    log.info("cardmarket: %d cards, %d boxes joined (guide %s)", len(cards), len(boxes), doc["guide_created_at"])
    return doc


def latest_day():
    d = store.DATA / "cardmarket"
    files = sorted(p.name for p in d.glob("????-??-??.json")) if d.exists() else []
    return store.read_json(d / files[-1]) if files else None
