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


def parse(source, text):
    """Apply the source's item regex; groups are (url, date, title) unless `order` says otherwise; named `title` wins."""
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
            is_product = src.get("kind") == "product" or bool(product.search(it["title"]))
            if first_run or not is_product or ignore.search(it["title"]):
                continue
            game = "Naruto" if it["game"] == "naruto" else "Fusion World"
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
