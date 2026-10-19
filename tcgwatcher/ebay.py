"""eBay slab layer (fase 5): graded prices (PSA/BGS/CGC 9–10) for the chase cards, next to raw TCGplayer prices.

Official Browse API with an application token (client-credentials; env EBAY_CLIENT_ID / EBAY_CLIENT_SECRET).
Per target card one search per marketplace (EBAY_DE, EBAY_NL — what you can actually buy from NL); titles are parsed for
the grade. Raw results of the day → data/ebay/<day>.json (immutable); summary per card key → used by build and alerts.

Signal ("slab-koop"): the cheapest PSA 10 (or 9) fixed-price listing costs no more than `slabs.max_vs_raw` × raw market
price — i.e. a graded copy for the price of a raw one (grading alone costs €20–30 and months).
"""
import base64
import datetime as dt
import logging
import os
import re
import time
import requests
from . import store, USER_AGENT
from .config import load_alerts

log = logging.getLogger("tcgwatcher.ebay")
API = "https://api.ebay.com"
GRADE = re.compile(r"\b(PSA|BGS|CGC|SGC|TAG|ACE)\s*-?\s*(10|9\.5|9)\b", re.I)
JP = re.compile(r"\b(japanese|japan|jp|jpn|日本)\b", re.I)
PROMO = re.compile(r"\b(winner|finalist|champion(ship)?|tournament|promo|event pack|pre-?release|release event|store championship)\b", re.I)
SAA_T = re.compile(r"(\bSAA\b|super\s*alt|god\s*rare|★★|\bV\.?3\b|super\s*parallel|special\s*alt)", re.I)
AA_T = re.compile(r"(alt(ernate)?[\s-]*art|\bparallel\b|\bAA\b|\bV\.?2\b|★)", re.I)
CLIP = 4.0
CHASE = ("SAA", "SCR_AA", "SCR", "SR_AA", "GDR", "SLR", "SPR")      # classes worth a slab/raw search
EU = {"AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL",
      "PL", "PT", "RO", "SK", "SI", "ES", "SE"}


class EbayClient:
    def __init__(self, client_id=None, secret=None, session=None):
        self.cid = (client_id or os.environ.get("EBAY_CLIENT_ID") or "").strip()
        self.sec = (secret or os.environ.get("EBAY_CLIENT_SECRET") or "").strip()
        self.s = session or requests.Session()
        self.s.headers.update({"User-Agent": USER_AGENT})
        self.token = None
        self.calls = 0

    @property
    def configured(self):
        return bool(self.cid and self.sec)

    def auth(self):
        basic = base64.b64encode(f"{self.cid}:{self.sec}".encode()).decode()
        r = self.s.post(f"{API}/identity/v1/oauth2/token", timeout=30,
                        headers={"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"},
                        data={"grant_type": "client_credentials", "scope": "https://api.ebay.com/oauth/api_scope"})
        r.raise_for_status()
        self.token = r.json()["access_token"]
        return self.token

    def search(self, q, marketplace="EBAY_DE", limit=50, sleep=0.3):
        if not self.token:
            self.auth()
        r = self.s.get(f"{API}/buy/browse/v1/item_summary/search", timeout=40,
                       params={"q": q, "limit": limit, "filter": "buyingOptions:{FIXED_PRICE}", "sort": "price"},
                       headers={"Authorization": f"Bearer {self.token}", "X-EBAY-C-MARKETPLACE-ID": marketplace,
                                "X-EBAY-C-ENDUSERCTX": "contextualLocation=country=NL,zip=5460"})
        self.calls += 1
        time.sleep(sleep)
        if r.status_code == 401:               # token expired mid-run
            self.auth()
            return self.search(q, marketplace, limit, sleep)
        r.raise_for_status()
        return r.json().get("itemSummaries", []) or []


def _price_eur(item, fx_usd_eur):
    p = item.get("price") or {}
    v, cur = float(p.get("value") or 0), p.get("currency")
    ship = 0.0
    for o in item.get("shippingOptions") or []:
        c = o.get("shippingCost") or {}
        if c.get("value") is not None:
            ship = float(c["value"]) if c.get("currency") == cur else ship
            break
    total = v + ship
    if cur == "EUR":
        return round(total, 2)
    if cur == "USD":
        return round(total * fx_usd_eur, 2)
    if cur == "GBP":
        return round(total * 1.15, 2)
    return None


def variant_matches(title, variant, number=""):
    """Same number ≠ same card: base SCR, Alternate Art (★) and Super Alternate Art (★★) share a number.
    Masters (BT…) has a real 'God Rare' variant; in Fusion World sellers use 'God Rare' as a synonym for ★★."""
    saa, aa = bool(SAA_T.search(title)), bool(AA_T.search(title))
    if number.upper().startswith("BT"):
        gdr = bool(re.search(r"(god\s*rare|\bGDR\b)", title, re.I))
        if variant == "GDR":
            return gdr
        if gdr:
            return False
    if variant == "GOLD":
        return bool(re.search(r"\bgold\b", title, re.I))
    if re.search(r"\bgold\b", title, re.I) and variant != "GOLD":
        return False
    if variant == "SAA":
        return saa
    if variant in ("AA", "BPAA"):
        return aa and not saa
    return not aa and not saa


def parse_listing(item, number, fx_usd_eur, variant=""):
    """One Browse item → {grade, grader, price_eur, url, title} when it is a graded copy of this exact card (number+variant)."""
    title = item.get("title") or ""
    if number.lower() not in title.lower():
        return None
    m = GRADE.search(title)
    if not m or JP.search(title) or PROMO.search(title) or not variant_matches(title, variant, number):
        return None
    price = _price_eur(item, fx_usd_eur)
    if not price:
        return None
    return {"grader": m.group(1).upper(), "grade": float(m.group(2)), "price": price, "title": title[:120],
            "url": item.get("itemWebUrl"), "item_id": item.get("itemId"), "marketplace": item.get("listingMarketplaceId"),
            "image": (item.get("image") or {}).get("imageUrl"),
            "location": (item.get("itemLocation") or {}).get("country"),
            "eu": ((item.get("itemLocation") or {}).get("country") or "").upper() in EU}


def cardtrader_slabs(ct_doc, blueprint_id, fx_unused=None, zero_fee=6.0, ship=5.0):
    """CardTrader graded listings of one blueprint → same shape as eBay listings (EU by construction)."""
    out = []
    g = ((ct_doc or {}).get("graded") or {}).get(str(blueprint_id))
    for l in (g or {}).get("listings", []):
        blob = " ".join(str(v) for v in l.get("props", {}).values()) + " " + (l.get("description") or "")
        m = GRADE.search(blob)
        if not m or JP.search(blob):
            continue
        price = round(l["price"] + (zero_fee if l.get("zero") else ship), 2)
        out.append({"grader": m.group(1).upper(), "grade": float(m.group(2)), "price": price, "title": f"CardTrader {blob[:100]}",
                    "url": f"https://www.cardtrader.com/cards/{blueprint_id}", "item_id": f"ct:{l.get('id')}", "marketplace": "CARDTRADER",
                    "location": l.get("country"), "eu": True, "zero": l.get("zero", False)})
    return out


# --- photo check: same number, different art -------------------------------------------------------------------
# Sellers mislabel ("God Rare" on a regular alt-art). Titles can't settle it; the picture can. Each card variant has an
# official TCGplayer image; a listing photo is assigned to the variant whose colour signature it resembles most.
# Signature = hue histogram of the saturated pixels in the central region (ignores white/grey backgrounds, sleeves and slab
# labels), compared by histogram intersection. Base vs alt-art vs super-alt-art vs gold differ in palette; verified on the
# FB10-123 case (a 'God Rare' listing whose photo was the green regular art: nearest = base, margin 0.045).
class ImageCheck:
    def __init__(self, session=None, timeout=20):
        self.s = session or requests.Session()
        self.s.headers.update({"User-Agent": USER_AGENT})
        self.timeout, self.cache, self.stats = timeout, {}, {"checked": 0, "rejected": 0, "unavailable": 0}

    @staticmethod
    def signature(data):
        from PIL import Image
        import io
        im = Image.open(io.BytesIO(data)).convert("RGB")
        w, h = im.size
        im = im.crop((int(w * 0.12), int(h * 0.12), int(w * 0.88), int(h * 0.88))).resize((48, 64))
        hist = [0.0] * 24
        for r, g, b in im.getdata():
            mx, mn = max(r, g, b), min(r, g, b)
            if mx < 38 or mx == 0 or (mx - mn) / mx < 0.25:      # dark or unsaturated: background, sleeve, label, text
                continue
            if mx == r:
                hue = ((g - b) / (mx - mn)) % 6
            elif mx == g:
                hue = (b - r) / (mx - mn) + 2
            else:
                hue = (r - g) / (mx - mn) + 4
            k = int((hue * 60) // 15) % 24
            hist[k] += 0.5; hist[(k - 1) % 24] += 0.25; hist[(k + 1) % 24] += 0.25     # soft bins: lighting shifts hue a little
        n = float(sum(hist)) or 1.0
        return [x / n for x in hist]

    @staticmethod
    def similarity(a, b):
        return sum(min(x, y) for x, y in zip(a, b))

    def sig_for(self, url):
        if not url:
            return None
        if url in self.cache:
            return self.cache[url]
        try:
            r = self.s.get(url, timeout=self.timeout)
            r.raise_for_status()
            sig = self.signature(r.content)
        except Exception:  # noqa: BLE001
            sig = None
        self.cache[url] = sig
        return sig

    def matches(self, listing_image_url, target_image_url, other_image_urls):
        """True when the photo resembles the target variant at least as much as any sibling variant (None = can't tell)."""
        li = self.sig_for(listing_image_url)
        tg = self.sig_for(target_image_url)
        if li is None or tg is None:
            self.stats["unavailable"] += 1
            return None
        self.stats["checked"] += 1
        mine = self.similarity(li, tg)
        for u in other_image_urls:
            o = self.sig_for(u)
            if o is not None and self.similarity(li, o) > mine + 0.03:
                self.stats["rejected"] += 1
                return False
        return True


def parse_raw_listing(item, number, fx_usd_eur, variant=""):
    """Ungraded copy of this exact card, located in the EU, price incl. shipping in €."""
    title = item.get("title") or ""
    if number.lower() not in title.lower() or GRADE.search(title) or JP.search(title) or PROMO.search(title):
        return None
    if re.search(r"\b(graded|slab|lot\b|bundle|playset|x\s?[2-9]\b|proxy|custom|damaged)", title, re.I) or not variant_matches(title, variant, number):
        return None
    if ((item.get("itemLocation") or {}).get("country") or "").upper() not in EU:
        return None
    price = _price_eur(item, fx_usd_eur)
    return {"price": price, "title": title[:120], "url": item.get("itemWebUrl"), "item_id": item.get("itemId"),
            "image": (item.get("image") or {}).get("imageUrl"),
            "location": (item.get("itemLocation") or {}).get("country")} if price else None


def targets(latest, per_set=10, min_raw_eur=20):
    """Cards worth watching: the most valuable market-priced hits per set (★★, ★, SCR, SR-AA), raw ≥ €20 —
    broad enough to catch cheap raws with expensive PSA 10s (grading candidates)."""
    out = []
    fx = latest["fx"]["usd_eur"]
    from .config import load_games
    cfg = load_games()
    for gid, g in latest["games"].items():
        gc = cfg.get(gid, {})
        if not gc.get("eu_sources", gid == "dbfw"):
            continue
        recent = sorted((s for s in g.get("sets", [])), key=lambda s: s.get("published") or "", reverse=True)[: gc.get("ebay_sets") or 999]
        allowed = {s["code"] for s in recent} if g.get("sets") else None
        by = {}
        for c in g.get("cards", []):
            if (allowed is None or c["set"] in allowed) and c["price"] and c["price_src"] == "market" and c["class"] in CHASE and c["price"] * fx >= min_raw_eur:
                by.setdefault(c["set"], []).append(c)
        for code, cs in by.items():
          cs.sort(key=lambda c: -c["price"])
          for c in cs[:per_set]:
            name = re.sub(r"\s*\(.*?\)|\s*-\s*(FB|SB|ST|BT)\d.*$|,.*$", "", c["name"]).split(" // ")[0].strip()
            sibl = [x for x in g.get("cards", []) if x["set"] == code and x["number"] == c["number"] and x["key"] != c["key"]]
            out.append({"key": c["key"], "number": c["number"], "name": name, "set": code, "class": c["class"], "variant": c.get("variant", ""),
                        "sibling_images": [x["image"] for x in sibl if x.get("image")],
                        "raw_eur": round(c["price"] * fx, 2), "image": c.get("image")})
    return out


def fetch_day(client=None, latest=None, marketplaces=("EBAY_DE", "EBAY_NL"), day=None, image_check=None):
    client = client or EbayClient()
    image_check = image_check if image_check is not None else ImageCheck()
    if not client.configured:
        log.info("no EBAY_CLIENT_ID/SECRET; skipping")
        return None
    day = day or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    path = store.DATA / "ebay" / f"{day}.json"
    if path.exists():
        log.info("ebay day file exists for %s; not refetching", day)
        return store.read_json(path)
    from .build import LATEST
    latest = latest or store.read_json(LATEST)
    fx = latest["fx"]["usd_eur"]
    if hasattr(client, "auth"):
        client.auth()                      # fail fast: a broken token setup must not produce an empty day file
    from .cardtrader import latest_day as ct_latest, eu_prices_by_tcgplayer
    ct_doc = ct_latest()
    ct_by_tcg = eu_prices_by_tcgplayer(ct_doc) if ct_doc else {}
    ct_bp = {int(b.get("tcg_player_id")): int(bid) for bid, b in ((ct_doc or {}).get("blueprints") or {}).items() if b.get("tcg_player_id")}
    cards = {}
    for t in targets(latest):
        listings, raws = [], []
        for mp in marketplaces:
            try:
                for it in client.search(f"{t['name']} {t['number']} PSA", mp):
                    p = parse_listing(it, t["number"], fx, t.get("variant", ""))
                    if p:
                        listings.append(p)
            except Exception as e:  # noqa: BLE001
                log.warning("%s %s: %s", t["number"], mp, e)
        try:                                                   # raw copies on eBay (EU sellers): where is the raw cheapest?
            for it in client.search(f"{t['name']} {t['number']}", marketplaces[0]):
                r = parse_raw_listing(it, t["number"], fx, t.get("variant", ""))
                if r:
                    raws.append(r)
        except Exception as e:  # noqa: BLE001
            log.warning("%s raw: %s", t["number"], e)
        if image_check and t.get("image") and t.get("sibling_images"):      # photo must look like THIS variant, not a sibling
            listings = [l for l in listings if image_check.matches(l.get("image"), t["image"], t["sibling_images"]) is not False]
            raws = [r for r in raws if image_check.matches(r.get("image"), t["image"], t["sibling_images"]) is not False]
        raws.sort(key=lambda x: x["price"])
        if raws and t["raw_eur"] and raws[0]["price"] < 0.25 * t["raw_eur"]:
            raws = [r for r in raws if r["price"] >= 0.25 * t["raw_eur"]]     # €3 'listings' for a €300 card are not that card
        if t.get("product_id") in ct_bp:
            listings += cardtrader_slabs(ct_doc, ct_bp[t["product_id"]])      # CardTrader graded section next to eBay
        seen, uniq = set(), []
        for l in sorted(listings, key=lambda x: x["price"]):
            if l["item_id"] in seen:
                continue
            seen.add(l["item_id"]); uniq.append(l)
        # Sellers often omit 'alt art'/'super alt art': a pricier variant then leaks into a cheaper one's group. Within one
        # grade, real prices cluster, so anything above CLIP × the group's lowest ask is treated as a mislabelled listing.
        clipped = []
        for grade in (10.0, 9.5, 9.0):
            g = [l for l in uniq if l["grade"] == grade]
            if g:
                low = g[0]["price"]
                clipped += [l for l in g if l["price"] <= CLIP * low]
        uniq = sorted(clipped, key=lambda x: x["price"])
        best, by_grader = {}, {}
        for grade in (10.0, 9.5, 9.0):
            g = [l for l in uniq if l["grade"] == grade]
            g_eu = [l for l in g if l.get("eu")]
            if g:
                best[str(grade)] = {"low": g[0]["price"], "n": len(g), "url": g[0]["url"], "grader": g[0]["grader"],
                                    "median": sorted(x["price"] for x in g)[len(g) // 2],
                                    "eu_low": g_eu[0]["price"] if g_eu else None, "eu_url": g_eu[0]["url"] if g_eu else None, "eu_n": len(g_eu)}
            for grader in {l["grader"] for l in g}:          # per grader: PSA 10 vs BGS 10 vs BGS 9.5 … are different markets
                gg = [l for l in g if l["grader"] == grader]
                by_grader.setdefault(grader, {})[str(grade)] = {"low": gg[0]["price"], "n": len(gg), "url": gg[0]["url"],
                                                                 "median": sorted(x["price"] for x in gg)[len(gg) // 2]}
        cards[t["key"]] = {**t, "best": best, "by_grader": by_grader, "listings": uniq[:25],
                           "raw_ebay": {"low": raws[0]["price"], "url": raws[0]["url"], "n": len(raws)} if raws else None}
    doc = {"day": day, "fetched_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "marketplaces": list(marketplaces), "api_calls": client.calls, "cards": cards,
           "image_check": image_check.stats if image_check else None}
    if not client.calls:
        raise RuntimeError("no successful eBay API calls; day file not written")
    store.write_json(path, doc)
    log.info("ebay: %d cards, %d graded listings, %d calls, photo check %s", len(cards), sum(len(c["listings"]) for c in cards.values()), client.calls,
             image_check.stats if image_check else "off")
    return doc


def latest_day():
    d = store.DATA / "ebay"
    files = sorted(p.name for p in d.glob("????-??-??.json")) if d.exists() else []
    return store.read_json(d / files[-1]) if files else None


def slab_signals(doc, cfg=None):
    """Slab buy signals: cheapest PSA10/9 ≤ max_vs_raw × raw. Returns list of dicts (one per card, best grade first)."""
    cfg = cfg or load_alerts().get("slabs", {"max_vs_raw": 1.15, "min_raw_eur": 50, "grades": [10, 9.5, 9]})
    out = []
    for key, c in (doc or {}).get("cards", {}).items():
        if c["raw_eur"] < cfg.get("min_raw_eur", 50):
            continue
        for grade in cfg.get("grades", [10, 9.5, 9]):
            b = c["best"].get(str(float(grade)))
            if b and b.get("eu_low") and b["eu_low"] <= cfg["max_vs_raw"] * c["raw_eur"]:     # EU-located: no import VAT/duties
                out.append({"key": key, "number": c["number"], "name": c["name"], "set": c["set"], "class": c["class"], "grade": grade,
                            "grader": b["grader"], "price": b["eu_low"], "raw_eur": c["raw_eur"], "ratio": round(b["eu_low"] / c["raw_eur"], 2),
                            "n": b["eu_n"], "url": b["eu_url"], "image": c.get("image")})
                break
    out.sort(key=lambda s: s["ratio"])
    return out


def grade_signals(doc, cfg=None):
    """Grading candidates per grading service: buy raw, grade, sell graded. Picks the service with the best expected net.

    Per service (config `grading.services`): outcome mix (share that comes back 10 / 9.5 / 9) × that service's median asks.
    net = expected_sale × (1 − fee_pct) − service cost − raw.  Signal when net ≥ min_profit and net/raw ≥ min_roi and the
    service's top grade has ≥ min_top_n listings (liquidity). Asks are a proxy for sales (Marketplace Insights is closed).
    """
    cfg = cfg or load_alerts().get("grading", {})
    fee = cfg.get("fee_pct", 13) / 100
    services = cfg.get("services") or {"PSA": {"cost_eur": 25, "mix": {"10": 0.4, "9": 0.6}}}
    out = []
    for key, c in (doc or {}).get("cards", {}).items():
        options = []
        for svc, sc in services.items():
            grades = (c.get("by_grader") or {}).get(svc) or {}
            mix = {str(float(k)): v for k, v in sc["mix"].items()}       # config "10" / "9.5" ↔ stored "10.0" / "9.5"
            top = max(mix, key=float)
            if not grades.get(top) or grades[top]["n"] < cfg.get("min_top_n", 3):
                continue
            expected, detail = 0.0, {}
            for grade, share in mix.items():
                g = grades.get(grade)
                price = g["median"] if g else (0.6 * grades[top]["median"])     # missing lower grade: conservative proxy
                expected += share * price
                detail[f"{float(grade):g}"] = round(price, 2)
            net = expected * (1 - fee) - sc["cost_eur"] - c["raw_eur"]
            options.append({"service": svc, "expected": round(expected, 2), "net": round(net, 2), "top_grade": f"{float(top):g}",
                            "top_n": grades[top]["n"], "top_median": grades[top]["median"], "url": grades[top]["url"], "prices": detail})
        if not options:
            continue
        options.sort(key=lambda o: -o["net"])
        o = options[0]
        roi = o["net"] / c["raw_eur"] if c["raw_eur"] else 0
        if o["net"] >= cfg.get("min_profit_eur", 50) and roi >= cfg.get("min_roi", 0.5):
            out.append({"key": key, "number": c["number"], "name": c["name"], "set": c["set"], "class": c["class"], "raw_eur": c["raw_eur"],
                        "service": o["service"], "top_grade": o["top_grade"], "top_median": o["top_median"], "top_n": o["top_n"],
                        "expected": o["expected"], "net": o["net"], "roi": round(roi, 2), "url": o["url"], "image": c.get("image"),
                        "options": options})
    out.sort(key=lambda s: -s["net"])
    return out
