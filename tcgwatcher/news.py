"""Official publisher news/product pages (config/news.yaml) → 📰 message for new product announcements.

Earliest "be first" signal: Bandai lists a set on its product page or news page before EU shops open pre-orders.
Raw items of every run are appended to data/news/<day>.json; seen urls live in data/state/news.json. Only titles matching
the `product` regex (and not `ignore`) are sent; everything else is stored for the page.
"""
import datetime as dt
import html
import logging
import re
from urllib.parse import urljoin
import yaml
from . import store, CONFIG
from .retail import Http

log = logging.getLogger("tcgwatcher.news")
SITE_URL = "https://np7editz-bot.github.io/TCGWatcher/"


def load_config():
    with open(CONFIG / "news.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _walk(o, out):
    """Collect dicts that look like articles inside a Next.js data blob: a title plus a slug/url/path, optionally a date."""
    if isinstance(o, dict):
        t = o.get("title")
        link = o.get("url") or o.get("path") or o.get("slug") or o.get("href")
        if isinstance(t, str) and isinstance(link, str) and 3 < len(t) < 200:
            date = o.get("date") or o.get("publishedAt") or o.get("publishDate") or o.get("published_at") or o.get("dateTime") or ""
            out.append({"title": t, "link": link, "date": str(date)[:10]})
        for v in o.values():
            _walk(v, out)
    elif isinstance(o, list):
        for v in o:
            _walk(v, out)


def parse_nextjs(source, text):
    """Riot-style sites render the article list from an inline JSON (`__NEXT_DATA__` or a Next.js stream); take every
    object with a title and a link under the source's `path_prefix` (e.g. /en-us/news/)."""
    import json
    m = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', text, re.S)
    blobs = [m.group(1)] if m else []
    if not blobs:                                   # app-router streams: pick up every JSON-looking script chunk
        blobs = re.findall(r'<script[^>]*type="application/json"[^>]*>(.*?)</script>', text, re.S)
    found = []
    for b in blobs:
        try:
            _walk(json.loads(html.unescape(b)), found)
        except Exception:  # noqa: BLE001
            continue
    prefix = source.get("path_prefix", "/news/")
    items, seen = [], set()
    for a in found:
        link = a["link"]
        if not link.startswith("http") and not link.startswith("/"):
            link = prefix.rstrip("/") + "/" + link
        url = urljoin(source["base"], link)
        if prefix not in url or url in seen:
            continue
        seen.add(url)
        items.append({"source": source["id"], "game": source["game"], "url": url, "title": a["title"][:200], "date": a["date"]})
    return items


def parse_anchors(source, text):
    """Layout-agnostic: every <a href> block that carries an ISO date is an article card (Riot's playriftbound.com lists
    '<a href=…><span>Announcements</span><time>2026-10-22T16:00:00Z</time><h3>Title</h3></a>'). Title = the longest text
    fragment inside the anchor that is not the date or a category label; links are kept when they sit under
    `path_prefix` or point at YouTube (Riot posts trailers as news)."""
    prefix = source.get("path_prefix", "/news/")
    items, seen = [], set()
    for m in re.finditer(r'<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', text, re.S | re.I):
        href, body = html.unescape(m.group(1)), m.group(2)
        d = re.search(r"(\d{4}-\d{2}-\d{2})T\d{2}:\d{2}", body)
        if not d:
            continue
        url = urljoin(source["base"], href)
        if url in seen or not (prefix in url or "youtu" in url):
            continue
        frags = [html.unescape(re.sub(r"\s+", " ", f)).strip() for f in re.split(r"<[^>]+>", body)]
        frags = [f for f in frags if f and not re.match(r"^\d{4}-\d{2}-\d{2}", f) and not re.match(r"^[A-Za-z]{3,20}$", f)]   # drop the date and one-word category labels
        if not frags:
            continue
        seen.add(url)
        items.append({"source": source["id"], "game": source["game"], "url": url, "title": max(frags, key=len)[:200], "date": d.group(1)})
    return items


def parse(source, text):
    """Apply the source's item regex; groups are (url, date, title) unless `order` says otherwise; named `title` wins."""
    if source.get("parser") == "nextjs":
        return parse_nextjs(source, text) or parse_anchors(source, text)
    if source.get("parser") == "anchors":
        return parse_anchors(source, text)
    order = source.get("order", ["url", "date", "title"])
    items, seen = [], set()
    for m in re.finditer(source["item"], text, re.I | re.S):
        g = dict(zip(order, m.groups()[: len(order)]))
        if m.groupdict().get("title"):
            g["title"] = m.group("title")
        url = urljoin(source["base"], html.unescape(g.get("url", "")))
        if not url or url in seen:
            continue
        seen.add(url)
        title = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", g.get("title") or "")).split())[:200]
        items.append({"source": source["id"], "game": source["game"], "url": url, "title": title, "date": (g.get("date") or "")[:10]})
    return items


def run(http=None, day=None, send=True):
    cfg = load_config()
    http = http or Http(sleep=1.0)
    day = day or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    state = store.read_json(store.state_path("news"), {"seen": {}})
    product, ignore = re.compile(cfg["product"], re.I), re.compile(cfg["ignore"], re.I)
    found, msgs, errors = [], [], {}
    for src in cfg["sources"]:
        text, err = http.get(src["url"])
        if text is None:
            errors[src["id"]] = err
            log.warning("%s: %s", src["id"], err)
            continue
        items = parse(src, text)
        if not items:
            errors[src["id"]] = "no items parsed (page layout changed?)"
            log.warning("%s: no items parsed", src["id"])
            continue
        first_run = not any(k.startswith(src["id"] + "|") for k in state["seen"])
        for it in items:
            it["fetched_at"] = now
            found.append(it)
            key = f"{src['id']}|{it['url']}"
            if key in state["seen"]:
                continue
            state["seen"][key] = {"title": it["title"], "date": it["date"], "seen": day}
            is_product = src.get("kind") in ("product", "all") or bool(product.search(it["title"]))
            if first_run or not is_product or ignore.search(it["title"]):
                continue
            if it.get("date") and (dt.date.fromisoformat(day) - dt.date.fromisoformat(it["date"][:10])).days > cfg.get("max_age_days", 14):
                continue                         # a repaired/new source must not replay weeks of old articles
            game = {"naruto": "Naruto", "riftbound": "Riftbound", "dbs": "Masters"}.get(it["game"], "Fusion World")
            msgs.append({"key": key, "kind": "news",
                         "text": f"📰 <b>{game} · officiële aankondiging</b>\n<b>{it['title']}</b>\n{src['name']} · {it['date'] or day}\n"
                                 f"<i>Dit is meestal hét moment waarop EU-shops pre-orders openen.</i>",
                         "buttons": [[("Bekijk", it["url"]), ("📊 Radar", SITE_URL)]]})
        log.info("%s: %d items", src["id"], len(items))
    path = store.DATA / "news" / f"{day}.json"
    prev = store.read_json(path, {"results": []})
    store.write_json(path, {"day": day, "fetched_at": now, "errors": errors, "results": prev.get("results", []) + found})
    store.write_json(store.state_path("news"), state, compact=False)
    if send and msgs:
        from . import telegram
        for m in msgs:
            telegram.send(m["text"], buttons=m["buttons"])
    return {"day": day, "items": len(found), "messages": msgs, "errors": errors}
