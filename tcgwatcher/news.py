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
        d = DATE_RX.search(body) or DATE_RX.search(text[max(0, m.start() - 250):m.start()])   # Bandai puts the date before the link
        if not d:
            continue
        url = urljoin(source["base"], href)
        if url in seen or not (prefix in url or "youtu" in url):
            continue
        h = re.search(r"<h\d[^>]*>(.*?)</h\d>", body, re.S)              # a headline beats the teaser text
        frags = [html.unescape(re.sub(r"\s+", " ", f)).strip() for f in re.split(r"<[^>]+>", (h.group(1) if h else body))]
        frags = [f for f in frags if f and not re.match(r"^\d{4}-\d{2}-\d{2}", f) and not re.match(r"^[A-Za-z]{3,20}$", f)]   # drop the date and one-word category labels
        if not frags:
            continue
        seen.add(url)
        items.append({"source": source["id"], "game": source["game"], "url": url, "title": max(frags, key=len)[:200], "date": _iso(d.group(1))})
    return items


EVENT = re.compile(r"(release\s*(event|tournament)|pre-?release|pre-?rift|ultra-?bout.*release|launch\s*event)", re.I)
CARDLIST = re.compile(r"card\s*list.*\[(DBS-)?(B|BT|FB|SB)\d{2}\]", re.I)
DRAWING = re.compile(r"(drawing|raffle|lottery|registration|register|loting|\bdraw\b)", re.I)
DATE_RX = re.compile(r"(\d{4}[-/.]\d{2}[-/.]\d{2})")


def _iso(d):
    return d.replace("/", "-").replace(".", "-")[:10]


def parse_youtube(source, text, http=None):
    """A channel's upload feed (Atom): `url` is the channel page (handle), from which the channel id is read, unless
    `channel_id` is configured. Social watch without an API: YouTube is where Bandai and Riot post reveals first."""
    cid = source.get("channel_id")
    if not cid:
        m = re.search(r'"channelId":"(UC[\w-]{20,})"', text) or re.search(r'channel_id=(UC[\w-]{20,})', text)
        if not m:
            return []
        cid = m.group(1)
    feed = text if "<feed" in text[:500] else None
    if feed is None:
        if http is None:
            return []
        feed, _ = http.get(f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}")
        if not feed:
            return []
    items = []
    for e in re.findall(r"<entry>(.*?)</entry>", feed, re.S):
        t = re.search(r"<title>(.*?)</title>", e, re.S); l = re.search(r'<link[^>]*href="([^"]+)"', e); d = re.search(r"<published>(\d{4}-\d{2}-\d{2})", e)
        if t and l:
            items.append({"source": source["id"], "game": source["game"], "url": l.group(1), "title": html.unescape(t.group(1)).strip()[:200], "date": d.group(1) if d else ""})
    return items


def parse(source, text, http=None):
    """Apply the source's item regex; groups are (url, date, title) unless `order` says otherwise; named `title` wins."""
    if source.get("parser") == "nextjs":
        return parse_nextjs(source, text) or parse_anchors(source, text)
    if source.get("parser") == "anchors":
        return parse_anchors(source, text)
    if source.get("parser") == "youtube":
        return parse_youtube(source, text, http)
    order = source.get("order", ["url", "date", "title"])
    items, seen = [], set()
    for m in re.finditer(source["item"], text, re.I | re.S):
        g = dict(zip(order, m.groups()[: len(order)]))
        if m.groupdict().get("title"):
            g["title"] = m.group("title")
        url = urljoin(source["base"], html.unescape(g.get("url", "")))
        if not url or url in seen:
            continue
        if not g.get("title"):
            g["title"] = re.sub(r"\.\w+$", "", url.rstrip("/").rsplit("/", 1)[-1]).replace("-", " ").replace("_", " ")
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
    health = state.setdefault("health", {})
    for src in cfg["sources"]:
        if src.get("enabled") is False:
            continue
        text, err = http.get(src["url"])
        if text is None:
            errors[src["id"]] = err
            log.warning("%s: %s", src["id"], err)
            continue
        items = [i for i in parse(src, text, http) if i["url"].rstrip("/") != src["url"].rstrip("/") and len(i["title"]) > 3]   # the index itself is not an article
        if not items:
            errors[src["id"]] = "no items parsed (page layout changed?)"
            log.warning("%s: no items parsed", src["id"])
            continue
        first_run = not any(k.startswith(src["id"] + "|") for k in state["seen"])
        for it in items:
            it["fetched_at"] = now
            found.append(it)
            key = f"{src['id']}|{it['url']}"
            prev = state["seen"].get(key)
            updated = bool(prev) and (it.get("date") or "") > (prev.get("date") or "") and it["title"] != prev.get("title")
            if prev and not updated:
                continue                         # same article, nothing new
            # Riot rewrites an article on the SAME url for the next product (product-drawing-faq: K/DA → T1 bundle,
            # 2026-11-06): a newer date with a new title is news again
            state["seen"][key] = {"title": it["title"], "date": it["date"], "seen": day}
            is_product = src.get("kind") in ("product", "all") or bool(product.search(it["title"]))
            if first_run or not is_product or ignore.search(it["title"]):
                continue
            if it.get("date") and (dt.date.fromisoformat(day) - dt.date.fromisoformat(it["date"][:10])).days > cfg.get("max_age_days", 14):
                continue                         # a repaired/new source must not replay weeks of old articles
            game = {"naruto": "Naruto", "riftbound": "Riftbound", "dbs": "Masters"}.get(it["game"], "Fusion World")
            hay = it["title"] + " " + it["url"]
            drawing, event, cards = bool(DRAWING.search(hay)), bool(EVENT.search(hay)), bool(CARDLIST.search(hay))
            if event:
                head = "🏁 <b>{g} · RELEASE-EVENT</b>"
                tail = ("Release-events: sealed spelen met packs van de nieuwe set + deelnamepromo's, vaak vóór de officiële release. "
                        "Inschrijven via Bandai TCG+ (Bandai Namco ID) of bij je lokale winkel — check de aanmelddatum in het artikel.")
            elif drawing:
                head = "🎟️ <b>{g} · loting / registratie</b>"
                tail = "Beperkte oplage via loting: registratie heeft een vast venster — check de data in het artikel."
            elif cards:
                head = "🃏 <b>{g} · kaartenlijst nieuwe set</b>"
                tail = "Zodra de kaarten geprijsd zijn rekent de radar de echte EV; tot die tijd staat er een indicatie op basis van eerdere sets."
            else:
                head = "📰 <b>{g} · officiële aankondiging</b>"
                tail = "Dit is meestal hét moment waarop EU-shops pre-orders openen."
            head = head.format(g=game)
            msgs.append({"key": key + ("|" + it["date"] if updated else ""), "kind": "news",
                         "text": f"{head}{' · 🔄 bijgewerkt' if updated else ''}\n<b>{it['title']}</b>\n{src['name']} · {it['date'] or day}\n"
                                 f"<i>{tail}</i>",
                         "buttons": [[("Bekijk", it["url"]), ("📊 Radar", SITE_URL)]]})
        log.info("%s: %d items", src["id"], len(items))
        newest = max((i.get("date") or "" for i in items), default="")
        if newest and (dt.date.fromisoformat(day) - dt.date.fromisoformat(newest[:10])).days > cfg.get("stale_days", 45):
            errors[src["id"]] = f"stale: newest item {newest} (layout changed or site moved?)"
    # watchdog: a source that errors, parses nothing or went stale is reported — once a week per source — so a silent
    # failure (Riot's site move, Oct 2026) cannot go unnoticed for weeks again
    for sid, err in errors.items():
        last = health.get(sid, {}).get("warned", "")
        if last and (dt.date.fromisoformat(day) - dt.date.fromisoformat(last)).days < 7:
            continue
        health[sid] = {"warned": day, "error": err}
        src = next((x for x in cfg["sources"] if x["id"] == sid), {})
        msgs.append({"key": f"health|{sid}|{day}", "kind": "status",
                     "text": f"⚠️ <b>Nieuwsbron stuk · {src.get('name', sid)}</b>\n{err}\n<i>Tot dit gefixt is, komen er geen 📰-berichten van deze bron.</i>",
                     "buttons": [[("Bron", src.get("url", SITE_URL))]]})
    for sid in list(health):
        if sid not in errors:
            health.pop(sid, None)                           # recovered: next failure warns again at once
    path = store.DATA / "news" / f"{day}.json"
    prev = store.read_json(path, {"results": []})
    store.write_json(path, {"day": day, "fetched_at": now, "errors": errors, "results": prev.get("results", []) + found})
    store.write_json(store.state_path("news"), state, compact=False)
    if send and msgs:
        from . import telegram
        for m in msgs:
            telegram.send(m["text"], buttons=m["buttons"])
    return {"day": day, "items": len(found), "messages": msgs, "errors": errors}
