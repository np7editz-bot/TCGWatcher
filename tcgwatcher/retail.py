"""Retailer monitor (fase 3a): price / availability / pre-order status of sealed product at EU web shops.

Strategy per shop (config/retailers.yaml):
  shopify  → <collection>/products.json (public JSON: title, handle, variants[].available, price)
  woo      → /wp-json/wc/store/v1/products?search=<q> (public Store API: prices, is_in_stock, is_purchasable)
  generic  → category HTML: links that look like Fusion World products; product HTML: OG price + status keywords
Each fetched product becomes a row in data/retail/<day>.json; data/state/retail.json keeps the last known status
per product URL so transitions (sold_out → in_stock, new listing, price drop) become alerts.
All fetched content is untrusted data: we only extract numbers and a few keywords from it.
"""
import datetime as dt
import html
import json
import logging
import re
import time
from urllib.parse import urljoin, urlparse
import requests
import yaml
from . import store, CONFIG, USER_AGENT

log = logging.getLogger("tcgwatcher.retail")

MATCH = re.compile(r"(fb\s?-?\d{2}|fs\s?-?\d{2}|sb\s?-?\d{2}|st\s?-?\d{2}|fusion[\s-]?world|dragon[\s-]?ball|naruto|riftbound|league of legends)", re.I)
# Riftbound sets carry no code in shop titles: name → our set code (TCGplayer abbreviations)
RIFT_SETS = {"origins": "OGN", "spiritforged": "SFD", "unleashed": "UNL", "vendetta": "VEN", "radiance": "RAD", "legacy": "LGC"}


def set_code_of(title, url=""):
    """Set code from a product title/url: Dragon Ball codes (FB10, BT26 …) or a Riftbound set name."""
    hay = f"{title or ''} {url or ''}"
    m = re.search(r"\b(FB|SB|ST|FS|BT|BE|SD|EB)\s?-?(\d{2})\b", hay, re.I)
    if m:
        return f"{m.group(1).upper()}{m.group(2)}"
    if re.search(r"(riftbound|league of legends)", hay, re.I):
        for name, code in RIFT_SETS.items():
            if re.search(r"\b" + name + r"\b", hay, re.I):
                return code
    return None
BOX = re.compile(r"(booster\s*box|display|booster\s*display|case|starter\s*deck|box)", re.I)
SOLD = re.compile(r"(uitverkocht|sold\s*out|ausverkauft|esaurito|agotado|épuisé|niet\s*(meer\s*)?(op\s*voorraad|leverbaar)|nicht\s*(mehr\s*)?verfügbar|out\s*of\s*stock|non\s*disponibile)", re.I)
PRE = re.compile(r"(pre-?\s?order|voorverkoop|vorbestell|pr[ée]-?commande|preordine|pre-?venta|coming\s*soon|binnenkort)", re.I)
INSTOCK = re.compile(r"(op\s*voorraad|in\s*stock|auf\s*lager|disponibile|en\s*stock|add\s*to\s*cart|in\s*winkelwagen|in\s*den\s*warenkorb)", re.I)
PRICE_META = re.compile(r'property="(?:product:price:amount|og:price:amount)"\s+content="([\d.,]+)"|content="([\d.,]+)"\s+property="(?:product:price:amount|og:price:amount)"', re.I)
KIND_BOX = re.compile(r"(booster\s*box|\bdisplay\b|bo[iî]te de 2\d|box of 2\d|\bbox\b|\bboxes\b|\bcaja\b)", re.I)
KIND_CASE = re.compile(r"\bcase\b", re.I)
KIND_DECK = re.compile(r"(starter|\bdeck\b)", re.I)
KIND_PACK = re.compile(r"(booster\s*pack|\bpacks?\b|sleeved|blister|\bbooster\b)", re.I)
KIND_OTHER = re.compile(r"(sleeves?\b|playmat|promo|binder|bundle|lot\b|collector'?s? display"
                        r"|proving grounds|jumbo|\bkit\b|gift box|box set|vault|showdown|chin[oe]s?e?\b|\bchino\b|acrylic)", re.I)   # not a booster box
# English market only: Japanese-language product is a different market (and much cheaper) → never a box/case for us
LANG_JP = re.compile(r"(\[\s*j[ap]\s*\]|\bjp\b|japan(ese|isch|s)?\b|\bjap\b|日本|-jp(-|$)|\(jp\))", re.I)


def kind_with_price(title, url, price):
    """Shops call a box 'Booster' (NL) or a pack 'Box' (IT): let the price break the tie."""
    k = kind_of(title, url)
    if price:
        if k == "pack" and price >= 60:
            return "box"
        if k == "box" and price < 25:
            return "pack"
    return k


def kind_of(title, url=""):
    """Classify a listing from its title (URL only as fallback). Order matters: accessories → case → deck → pack → box."""
    t = title or ""
    if not t.strip():
        t = url.rsplit("/", 1)[-1].replace("-", " ")
    if KIND_OTHER.search(t) or LANG_JP.search(t) or LANG_JP.search(url.rstrip("/").rsplit("/", 1)[-1]):
        return "other"
    if KIND_CASE.search(t):
        return "case"
    if KIND_DECK.search(t):
        return "deck"
    if KIND_BOX.search(t):
        return "box"
    if KIND_PACK.search(t):
        return "pack"
    slug = url.rstrip("/").rsplit("/", 1)[-1].replace("-", " ")
    if slug and slug != t:          # title without a product word ('FB12 Reach the God (ENG)'): let the URL slug decide
        return kind_of(slug)
    return "other"


BUY_BTN = re.compile(r"<button\b([^>]*(?:add[-_ ]?to[-_ ]?cart|cart[-_]?btn|buy|acquista|kopen|warenkorb|panier|carrito)[^>]*)>(.*?)</button>", re.I | re.S)
PRICE_TXT = re.compile(r"(?:€|EUR|£)\s?(\d{1,4}(?:[.,]\d{3})*(?:[.,]\d{2})?)|(\d{1,4}(?:[.,]\d{2}))\s?(?:€|EUR)")


def load_retailers():
    with open(CONFIG / "retailers.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


class Http:
    def __init__(self, sleep=1.0):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "nl,en;q=0.8,de;q=0.6"})
        self.sleep = sleep

    def get(self, url, json_=False):
        try:
            r = self.s.get(url, timeout=40)
            time.sleep(self.sleep)
            if r.status_code != 200:
                return None, f"http {r.status_code}"
            return (r.json() if json_ else r.text), None
        except Exception as e:  # noqa: BLE001
            return None, f"error {type(e).__name__}"


def _num(s):
    if s is None:
        return None
    s = str(s).strip().replace(" ", "")
    if re.match(r"^\d{1,3}(\.\d{3})+(,\d{2})?$", s):      # 1.234,56
        s = s.replace(".", "").replace(",", ".")
    elif re.match(r"^\d+,\d{2}$", s):                       # 134,99
        s = s.replace(",", ".")
    else:
        s = s.replace(",", "")
    try:
        return round(float(s), 2)
    except ValueError:
        return None


NOTIFY = re.compile(r"(benachrichtige mich|notify me|e-?mail me when|houd mij op de hoogte|laat het me weten|avvisami|avísame|prévenez-moi|back in stock notification)", re.I)


def status_from_text(text):
    t = text[:200000]
    if NOTIFY.search(t):                      # 'notify me when available' form = the shop itself says: not buyable now
        return "sold_out"
    if SOLD.search(t) and not INSTOCK.search(t):
        return "sold_out"
    so, ins = SOLD.search(t), INSTOCK.search(t)
    if so and ins and so.start() < ins.start():   # product's own 'sold out' comes before the related products' 'add to cart'
        return "sold_out"
    if PRE.search(t):
        return "preorder"
    if INSTOCK.search(t):
        return "in_stock"
    if SOLD.search(t):
        return "sold_out"
    return "unknown"


# --- platform extractors -------------------------------------------------------------------------
def shopify_collection(http, retailer):
    rows, err = [], None
    for cat in [retailer["category"]] + list(retailer.get("categories") or []):
        got, e = _shopify_one(http, dict(retailer, category=cat))
        if got:
            rows += got
        err = err or e
    return (rows, None) if rows else (None, err)


def _shopify_one(http, retailer):
    base = retailer["category"].split("?")[0].rstrip("/")
    m = re.match(r"^(https?://[^/]+(?:/[a-z]{2}(?:-[a-z]{2})?)?/collections/[^/]+)", base, re.I)
    if m:
        base = m.group(1)
    rows = []
    for page in (1, 2):
        data, err = http.get(f"{base}/products.json?limit=250&page={page}", json_=True)
        if not isinstance(data, dict) or not data.get("products"):
            if page == 1:
                return None, err or "not shopify"
            break
        origin = "{0.scheme}://{0.netloc}".format(urlparse(base))
        for p in data["products"]:
            title = p.get("title", "")
            if not MATCH.search(title):
                continue
            avail = any(v.get("available") for v in p.get("variants", []))
            prices = [_num(v.get("price")) for v in p.get("variants", []) if v.get("price")]
            url = f"{origin}/products/{p.get('handle')}"
            status = "in_stock" if avail else "sold_out"
            if avail and PRE.search(title + " " + (p.get("tags") and " ".join(p["tags"]) or "")):
                status = "preorder"
            rows.append(_row(retailer, url, title, min(prices) if prices else None, status, "shopify"))
        if len(data["products"]) < 250:
            break
    return rows, None


def woo_store(http, retailer):
    origin = "{0.scheme}://{0.netloc}".format(urlparse(retailer["category"]))
    rows = []
    seen = set()
    for q in ("fusion world", "dragon ball", "naruto", "riftbound"):
        data, err = http.get(f"{origin}/wp-json/wc/store/v1/products?search={q.replace(' ', '%20')}&per_page=100", json_=True)
        if not isinstance(data, list):
            if err and q == "fusion world":
                return None, err
            continue
        for p in data:
            url = p.get("permalink") or ""
            if url in seen or not MATCH.search(p.get("name", "")):
                continue
            seen.add(url)
            pr = p.get("prices") or {}
            minor = int(pr.get("currency_minor_unit", 2))
            price = _num(pr.get("price")) / (10 ** minor) if pr.get("price") else None
            if p.get("is_in_stock") and p.get("is_purchasable"):
                status = "preorder" if PRE.search(p.get("name", "") + " " + str(p.get("short_description", ""))) else "in_stock"
            else:
                status = "sold_out"
            rows.append(_row(retailer, url, html.unescape(p.get("name", "")), price, status, "woo"))
    return rows, None


def generic_category(http, retailer):
    text, err = http.get(retailer["category"])
    if text is None:
        return None, err
    base = retailer["category"]
    links = {}
    for m in re.finditer(r'<a\s[^>]*href="([^"#]+)"[^>]*>(.*?)</a>', text, re.I | re.S):
        href, inner = m.group(1), re.sub(r"<[^>]+>", " ", m.group(2))
        url = urljoin(base, html.unescape(href))
        if urlparse(url).netloc != urlparse(base).netloc:
            continue
        label = html.unescape(" ".join(inner.split()))[:160]
        hay = label + " " + url
        if MATCH.search(hay) and BOX.search(hay) and url != base:
            links.setdefault(url, label)
    rows = []
    for url, label in list(links.items())[:40]:
        rows.append(_row(retailer, url, label or url, None, "unknown", "generic-category"))
    return rows, None


def generic_product(http, row, retailer=None):
    text, err = http.get(row["url"])
    if text is None:
        row["error"] = err
        return row
    m = PRICE_META.search(text)
    price = _num(m.group(1) or m.group(2)) if m else None
    if price is None:
        m = PRICE_TXT.search(re.sub(r"<[^>]+>", " ", text))
        price = _num(m.group(1) or m.group(2)) if m else None
    t = re.search(r"<title>(.*?)</title>", text, re.I | re.S)
    if t and (not row.get("title") or row["title"] == row["url"]):
        row["title"] = html.unescape(" ".join(t.group(1).split()))[:160]
    mult = (retailer or {}).get("price_multiplier")
    price = None if (price is not None and price <= 0) else price
    row["price"] = round(price * mult, 2) if (price is not None and mult) else price
    av = re.search(r'(?:product:availability|og:availability)"\s+content="([^"]+)"|"availability"\s*:\s*"(?:https?://schema\.org/)?(\w+)"', text, re.I)
    val = ((av.group(1) or av.group(2)) if av else "").lower()
    if "instock" in val or val == "in stock":
        row["status"] = "in_stock"
    elif "preorder" in val or "pre-order" in val:
        row["status"] = "preorder"
    elif "outofstock" in val or "out of stock" in val or "soldout" in val:
        row["status"] = "sold_out"
    else:
        row["status"] = status_from_text(re.sub(r"<script.*?</script>|<style.*?</style>|<nav.*?</nav>|<header.*?</header>|<footer.*?</footer>", " ", text, flags=re.S | re.I))
    # The buy button is the truth: a shop can keep schema.org 'PreOrder' while the button says 'Out of stock' (Ony TCG FB11).
    for attrs, inner in BUY_BTN.findall(text):
        label = re.sub(r"<[^>]+>", " ", inner)
        if re.search(r"\bdisabled\b", attrs, re.I) or SOLD.search(label):
            row["status"] = "sold_out"
            break
    ot = re.search(r'property="og:title"\s+content="([^"]+)"', text, re.I)
    if ot:
        row["title"] = html.unescape(ot.group(1))[:160]
    row["source"] = "generic-product"
    row["kind"] = kind_with_price(row.get("title") or "", row["url"], row.get("price"))
    return row


def _row(retailer, url, title, price, status, source):
    mult = retailer.get("price_multiplier")          # e.g. 1.21 when a shop exposes ex-VAT prices in meta tags
    if price is not None and mult and source.startswith("generic"):
        price = round(price * mult, 2)
    return {"retailer": retailer["id"], "country": retailer.get("country"), "url": url, "title": title,
            "price": price, "currency": retailer.get("currency", "EUR"), "status": status, "source": source,
            "kind": kind_with_price(title or "", url, price)}


# --- orchestration -------------------------------------------------------------------------------
def run(day=None, http=None, retailer_ids=None):
    cfg = load_retailers()
    http = http or Http()
    day = day or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows, errors = [], {}
    for r in cfg["retailers"]:
        if retailer_ids and r["id"] not in retailer_ids:
            continue
        if not r.get("category") or (r.get("enabled", True) is False and not retailer_ids):
            continue          # an explicit selection (CLI/tests) may still poll a disabled shop
        platform = r.get("platform", "auto")
        got, err = None, None
        if platform in ("shopify", "auto"):
            got, err = shopify_collection(http, r)
        if not got and platform in ("woo", "auto"):
            got, err = woo_store(http, r)
        if not got:
            got, err = generic_category(http, r)
            if got:
                got = [generic_product(http, x, r) for x in got[:15]]   # bounded per shop
        if got is None:
            errors[r["id"]] = err or "no data"
            log.warning("%s: %s", r["id"], err)
            continue
        for x in got:
            x["fetched_at"] = now
            x["trusted"] = bool(r.get("enabled", True)) and not x.get("source", "").startswith("generic")
        rows.extend(got)
        log.info("%s: %d products (%s)", r["id"], len(got), got[0]["source"] if got else "-")
    doc = {"day": day, "fetched_at": now, "errors": errors, "results": rows}
    path = store.DATA / "retail" / f"{day}.json"
    prev = store.read_json(path, {"results": []})
    # append-only within a day: keep earlier fetches of the same day, newest observation per url wins in state
    doc["results"] = prev.get("results", []) + rows
    store.write_json(path, doc)
    transitions = update_state(rows, now, succeeded={r["retailer"] for r in rows})
    return {"day": day, "rows": len(rows), "errors": errors, "transitions": transitions}


def update_state(rows, now, succeeded=()):
    state = store.read_json(store.state_path("retail"), {"products": {}})
    prods = state["products"]
    seen_urls = {r["url"] for r in rows}
    for url in [u for u, p in prods.items() if p["retailer"] in succeeded and u not in seen_urls]:
        prods.pop(url)          # listing disappeared from a shop that answered → drop (no alert; sold_out is the signal)
    transitions = []
    for r in rows:
        key = r["url"]
        old = prods.get(key)
        new = {"retailer": r["retailer"], "title": r["title"], "price": r["price"], "status": r["status"], "seen": now, "product_kind": r.get("kind"),
               "trusted": r.get("trusted", False),
               "first_seen": old["first_seen"] if old else now,
               "price_obs": ((old.get("price_obs") or 1) + 1) if old and old.get("price") == r["price"] else 1}   # how often this price was seen
        if old is None:
            transitions.append({"kind": "new_listing", **new, "url": key})
        else:
            if old["status"] != new["status"] and new["status"] != "unknown":
                transitions.append({"kind": f"{old['status']}->{new['status']}", "old_price": old.get("price"), **new, "url": key})
            elif old.get("price") and new["price"] and new["price"] < old["price"] * 0.9 and (old.get("price_obs") or 1) >= 2:
                transitions.append({"kind": "price_drop", "old_price": old["price"], **new, "url": key})
            elif old.get("price") and new["price"] and new["price"] > old["price"] * 1.1:
                transitions.append({"kind": "price_rise", "old_price": old["price"], **new, "url": key})
        prods[key] = new
    store.write_json(store.state_path("retail"), state, compact=False)
    return transitions


KIND_TEXT = {
    "new_listing": "🆕 nieuw gelist",
    "sold_out->in_stock": "✅ terug op voorraad",
    "sold_out->preorder": "🟡 pre-order open",
    "unknown->in_stock": "✅ op voorraad",
    "unknown->preorder": "🟡 pre-order open",
    "in_stock->sold_out": "⛔ uitverkocht",
    "preorder->sold_out": "⛔ pre-order dicht / uitverkocht",
    "preorder->in_stock": "✅ geleverd, op voorraad",
    "price_drop": "📉 prijs omlaag",
    "price_rise": "📈 prijs omhoog",
}


def format_alerts(transitions, first_run, muted=(), latest=None, buy=None):
    """Only actionable messages: a box/case that becomes buyable (in stock or pre-order) and is worth it.

    worth it = EV€ of hits ÷ this price ≥ buy.near, or the set is not released/scored yet (new set, Naruto).
    Sold-out, price rises and plain new listings stay in data/retail/<day>.json and on the page.
    """
    if first_run:
        return [{"text": f"🏪 Retailer-monitor gestart: {len(transitions)} producten gevonden bij "
                         f"{len({t['retailer'] for t in transitions})} shops. Vanaf nu meld ik alleen koopwaardige voorraad en pre-orders.", "buttons": None}]
    import re as _re
    buy = buy or {"threshold": 0.8, "near": 0.7}
    sets, silent = {}, set()
    if latest:
        for g in latest.get("games", {}).values():
            for st in g.get("sets", []):
                sets[st["code"]] = st
                if not g.get("alerts", True):
                    silent.add(st["code"])                 # silent game (Masters): no retail messages either
    out = []
    for t in transitions:
        if not t.get("trusted"):
            continue          # HTML-heuristic shops: data only, never a message (Comic Planet 'pre-order' on a dead page)
        hay = (t.get("title") or "") + " " + t["url"]
        naruto = bool(re.search(r"naruto", hay, re.I)) and bool(re.search(r"(card ?game|\btcg\b|booster|\bdeck\b|starter|display|\bpack)", hay, re.I)) \
            and not re.search(r"(funko|\bpop!?\b|figur|plush|knuffel|manga\b|poster|keychain|shirt|\bmug\b|statue|nendoroid|blu-?ray|dvd)", hay, re.I)   # a card-game product, not merchandise
        if re.search(r"naruto", hay, re.I) and not naruto:
            continue
        k = kind_with_price(t.get("title") or "", t["url"], t.get("price")) if t.get("product_kind") is not None else None   # recomputed: stored kind may predate a rule fix
        if not naruto and k not in (None, "box", "case"):
            continue                                   # Naruto: every product counts (decks, packs, boxes) — launch lineup
        if not t.get("price"):
            continue
        becomes_buyable = t["status"] in ("in_stock", "preorder") and (t["kind"] == "new_listing" or t["kind"].endswith(f"->{t['status']}"))
        cheaper = t["kind"] == "price_drop"
        if not (becomes_buyable or cheaper):
            continue
        code = set_code_of(t.get("title"), t["url"])
        if code and (code in muted or code in silent):
            continue
        st = None if naruto else sets.get(code)
        ratio = None
        if st and st.get("ev_eu_excl_saa") and "no_cards" not in st["flags"]:
            per_box = t["price"] / 12 if t.get("product_kind") == "case" else t["price"]
            ratio = st["ev_eu_excl_saa"] / per_box
            if ratio < buy["near"] or any(f in st["flags"] for f in ("manga", "story", "low_coverage")):
                continue          # buyable, but not worth a message
            if cheaper and ratio < buy["threshold"]:
                continue
        elif cheaper:
            continue              # price drop on an unscored set: not actionable
        label = {"preorder": "🟡 PRE-ORDER OPEN", "in_stock": "✅ OP VOORRAAD"}[t["status"]] if becomes_buyable else "📉 GOEDKOPER"
        if naruto:
            label = "🍥 NARUTO · " + label
        price = f"€{t['price']:.2f}".replace(".", ",")
        old = f" (was €{t['old_price']:.2f})".replace(".", ",") if cheaper and t.get("old_price") else ""
        verdict = (f"Ratio <b>{ratio:.2f}</b> → {'koopwaardig' if ratio >= buy['threshold'] else 'bijna koopwaardig'} (hits €{st['ev_eu_excl_saa']:.0f} per box)"
                   if ratio is not None else "Nog geen waardering (set niet uit) — pre-order = eerste kans")
        out.append({"text": f"{label} · <b>{code or 'sealed'}</b>\n{t['title'][:90]}\n<b>{price}</b> bij {t['retailer']}{old}\n{verdict}",
                    "buttons": [[(f"🛒 {t['retailer']}", t["url"]), ("📊 Radar", "https://np7editz-bot.github.io/TCGWatcher/")]]})
    return out
