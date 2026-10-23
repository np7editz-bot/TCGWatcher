"""One-off discovery for a new game, run in the Action (the container here has no network): what does each source call it?
Writes data/probe/<slug>.json with TCGplayer groups + rarity/name samples, CardTrader game + expansions, the Cardmarket
game id (scan of the public catalogue), and the raw HTML head of candidate news pages — enough to write the config block.
`python -m tcgwatcher probe --category 89 --slug riftbound --cm-name Riftbound --news https://riftbound.com/en-us/news`"""
import collections
import logging
import re
import requests
from . import store, USER_AGENT

log = logging.getLogger("tcgwatcher.probe")
TCGCSV = "https://tcgcsv.com/tcgplayer"


def shops(needle):
    """Where do our retailers sell this game? Shopify: /collections.json handles; Woo: Store API search count."""
    from .config import ROOT
    import yaml
    from urllib.parse import urlparse
    s = requests.Session(); s.headers.update({"User-Agent": USER_AGENT})
    cfg = yaml.safe_load(open(ROOT / "config" / "retailers.yaml"))["retailers"]
    out = {}
    for r in cfg:
        if r.get("enabled") is False:
            continue
        origin = "{0.scheme}://{0.netloc}".format(urlparse(r["category"]))
        try:
            if r.get("platform") == "shopify":
                cols = []
                for page in (1, 2, 3):
                    d = s.get(f"{origin}/collections.json?limit=250&page={page}", timeout=30).json().get("collections", [])
                    cols += d
                    if len(d) < 250:
                        break
                hits = [{"handle": c["handle"], "title": c.get("title"), "n": c.get("products_count")} for c in cols if needle in (c.get("handle", "") + " " + (c.get("title") or "")).lower()]
                out[r["id"]] = {"platform": "shopify", "collections": hits, "total_collections": len(cols)}
            elif r.get("platform") == "woo":
                d = s.get(f"{origin}/wp-json/wc/store/v1/products?search={needle}&per_page=50", timeout=30).json()
                out[r["id"]] = {"platform": "woo", "n": len(d) if isinstance(d, list) else None, "sample": [p.get("name") for p in d[:8]] if isinstance(d, list) else str(d)[:120]}
            else:
                d = s.get(f"{origin}", timeout=30)
                out[r["id"]] = {"platform": r.get("platform"), "home_mentions": d.text.lower().count(needle)}
        except Exception as e:  # noqa: BLE001
            out[r["id"]] = {"error": str(e)[:120]}
    return out


def run(category, slug, cm_name=None, news_urls=(), ct_name=None):
    s = requests.Session(); s.headers.update({"User-Agent": USER_AGENT})
    out = {"category": category}
    try:
        out["shops"] = shops(slug.lower())
    except Exception as e:  # noqa: BLE001
        out["shops_error"] = str(e)[:120]
    groups = s.get(f"{TCGCSV}/{category}/groups", timeout=60).json().get("results", [])
    out["groups"] = [{"groupId": g["groupId"], "abbreviation": g.get("abbreviation"), "name": g.get("name"), "publishedOn": (g.get("publishedOn") or "")[:10]} for g in groups]
    rar, samples, sealed = collections.Counter(), collections.defaultdict(list), []
    for g in groups:
        prods = s.get(f"{TCGCSV}/{category}/{g['groupId']}/products", timeout=60).json().get("results", [])
        for p in prods:
            ext = {e["name"]: e["value"] for e in p.get("extendedData", [])}
            if ext.get("Number"):
                key = (g.get("abbreviation") or g["name"], ext.get("Rarity") or "?")
                rar[key] += 1
                if len(samples[key]) < 4:
                    samples[key].append({"name": p["name"], "number": ext.get("Number")})
            else:
                sealed.append({"group": g.get("abbreviation") or g["name"], "name": p["name"], "productId": p["productId"]})
    out["rarities"] = [{"group": k[0], "rarity": k[1], "n": v, "samples": samples[k]} for k, v in sorted(rar.items())]
    out["sealed"] = sealed
    # name suffix variants: '(…)' at the end of card names
    suf = collections.Counter()
    for g in groups:
        for p in s.get(f"{TCGCSV}/{category}/{g['groupId']}/products", timeout=60).json().get("results", []):
            m = re.search(r"\(([^()]+)\)\s*$", p["name"])
            if m and any(e["name"] == "Number" for e in p.get("extendedData", [])):
                suf[m.group(1)] += 1
    out["name_suffixes"] = suf.most_common(40)
    # CardTrader: game + expansions
    try:
        from .cardtrader import CardTrader
        ct = CardTrader()
        if ct.token:
            games = ct.get("/games")
            hits = [g for g in games if (ct_name or slug).lower() in (g.get("display_name") or g.get("name") or "").lower()]
            out["cardtrader_games"] = [{"id": g["id"], "name": g.get("display_name") or g.get("name")} for g in hits]
            if hits:
                exps = [e for e in ct.get("/expansions") if e["game_id"] in {g["id"] for g in hits}]
                out["cardtrader_expansions"] = [{"id": e["id"], "code": e.get("code"), "name": e.get("name")} for e in exps]
    except Exception as e:  # noqa: BLE001
        out["cardtrader_error"] = str(e)
    # Cardmarket: which game id has this name in its sealed catalogue?
    if cm_name:
        found = {}
        for gid in range(1, 41):
            try:
                r = s.get(f"https://downloads.s3.cardmarket.com/productCatalog/productList/products_nonsingles_{gid}.json", timeout=60)
                if r.status_code != 200:
                    continue
                prods = r.json().get("products", [])
                names = [p["name"] for p in prods if cm_name.lower() in p["name"].lower()]
                if names or any(cm_name.lower() in (p.get("categoryName") or "").lower() for p in prods[:50]):
                    found[gid] = {"n_products": len(prods), "matches": names[:15], "category_sample": (prods[0].get("categoryName") if prods else None)}
            except Exception as e:  # noqa: BLE001
                found[gid] = {"error": str(e)[:80]}
        out["cardmarket_candidates"] = found
    # news pages: raw head so the item regex can be written from it
    out["news"] = {}
    for u in news_urls:
        try:
            r = s.get(u, timeout=60)
            out["news"][u] = {"status": r.status_code, "head": r.text[:6000]}
        except Exception as e:  # noqa: BLE001
            out["news"][u] = {"error": str(e)[:120]}
    store.write_json(store.DATA / "probe" / f"{slug}.json", out, compact=False)
    log.info("probe %s: %d groups, %d rarity buckets, %d sealed, cm candidates %s", slug, len(groups), len(rar), len(sealed), list((out.get("cardmarket_candidates") or {}).keys()))
    return out
