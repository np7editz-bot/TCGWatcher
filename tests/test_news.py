import json
from tcgwatcher import news, store

DBFW_NEWS = '''<ul class="newsList">
<li class="newsItem"><div class="cardBGCol"></div><a href="https://www.dbs-cardgame.com/fw/en/news/02_340.html" class="newsLink"></a>
<div class="newsDetail"><div class="newsDetailHead"><div class="newsCategory newsCatNews"><p>NEWS</p></div>
<time class="newsDate" datetime="2026-10-17">October 17, 2026</time></div><div class="newsDetailBottom">
<h3 class="newsText">[DIGITAL ver.] [Advance Notice] &quot;BOOSTER PACK BRIGHTNESS OF HOPE [FB11]&quot; Coming Soon!</h3></div></div></li>
<li class="newsItem"><a href="https://www.dbs-cardgame.com/fw/en/news/02_350.html" class="newsLink"></a>
<time class="newsDate" datetime="2026-10-20">October 20, 2026</time><h3 class="newsText">BOOSTER PACK -REACH THE GOD- [FB12] Release Date Announced</h3></li>
<li class="newsItem"><a href="https://www.dbs-cardgame.com/fw/en/news/01_524.html" class="newsLink"></a>
<time class="newsDate" datetime="2026-10-07">October 07, 2026</time><h3 class="newsText">Digital Ver. Serial Code Expiry Date Notice</h3></li>
</ul>'''
DBFW_PRODUCTS = '''<a href="https://www.dbs-cardgame.com/fw/en/products/01_422.html"><img src="x.webp"><p>BOOSTER PACK -BRIGHTNESS OF HOPE- [FB11]</p></a>
<a href="https://www.dbs-cardgame.com/fw/en/products/01_401.html"><p>STORY BOOSTER 01 [ST01]</p></a>'''
NARUTO = '''<li class="newsListItem card"> <a class="cardBox" href="/en/news/nycc-report.php"> <div class="cardThumb"></div>
<dl class="cardData"> <dt class="cardTitle">NARUTO CARD GAME New Info Revealed! Oct. 2026 Event Information</dt>
<dd class="cardInfo"> <div class="category">EVENTS</div> <time class="date" datetime="2026-10-13">Oct. 13, 2026</time> </dd> </dl> </a> </li>
<li class="newsListItem card"> <a class="cardBox" href="/en/news/starter-decks.php"><dt class="cardTitle">Starter Deck lineup &amp; pre-order information</dt>
<time class="date" datetime="2026-11-02">Nov. 02, 2026</time></a></li>'''


class FakeHttp:
    def __init__(self, pages):
        self.pages = pages

    def get(self, url, json_=False):
        return (self.pages.get(url), None) if url in self.pages else (None, "http 404")


def pages(dbfw_news=DBFW_NEWS):
    return {"https://www.dbs-cardgame.com/fw/en/news.html": dbfw_news,
            "https://www.dbs-cardgame.com/fw/en/products/": DBFW_PRODUCTS,
            "https://www.naruto-cardgame.com/en/news/article-list.php": NARUTO}


def test_parse_all_three_layouts():
    cfg = news.load_config()
    by = {s["id"]: s for s in cfg["sources"]}
    n = news.parse(by["dbfw_news"], DBFW_NEWS)
    assert [i["date"] for i in n] == ["2026-10-17", "2026-10-20", "2026-10-07"] and n[1]["title"].startswith("BOOSTER PACK -REACH THE GOD-")
    p = news.parse(by["dbfw_products"], DBFW_PRODUCTS)
    assert [i["title"] for i in p] == ["BOOSTER PACK -BRIGHTNESS OF HOPE- [FB11]", "STORY BOOSTER 01 [ST01]"]
    r = news.parse(by["naruto_news"], NARUTO)
    assert r[0]["url"] == "https://www.naruto-cardgame.com/en/news/nycc-report.php" and r[0]["date"] == "2026-10-13"
    assert r[1]["title"] == "Starter Deck lineup & pre-order information"


def test_first_run_silent_then_only_product_news(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA", tmp_path)
    first = news.run(http=FakeHttp(pages(DBFW_NEWS.split('<li class="newsItem"><a href="https://www.dbs-cardgame.com/fw/en/news/02_350')[0] + "</ul>")),
                     day="2026-10-19", send=False)
    news_only = lambda r: [m for m in r["messages"] if m["kind"] != "status"]   # the fake http 404s the other sources → ⚠️ watchdog
    assert first["items"] == 5 and news_only(first) == []          # first run: learn, don't spam
    second = news.run(http=FakeHttp(pages()), day="2026-10-20", send=False)
    texts = [m["text"] for m in news_only(second)]
    assert len(texts) == 1 and "REACH THE GOD" in texts[0] and "Fusion World" in texts[0]
    # digital-app notice and events are stored, not sent; nothing repeats
    assert news_only(news.run(http=FakeHttp(pages()), day="2026-10-21", send=False)) == []
    day = json.load(open(tmp_path / "news" / "2026-10-20.json"))
    assert len(day["results"]) == 7


def test_nextjs_parser_finds_articles_in_inline_json():
    import json
    from tcgwatcher.news import parse
    page = '<html><script id="__NEXT_DATA__" type="application/json">' + json.dumps(
        {"props": {"pageProps": {"articles": [{"title": "Radiance is here", "slug": "radiance-is-here", "publishedAt": "2026-10-23T10:00:00Z"},
                                              {"title": "Patch notes", "url": "/en-us/news/game-updates/patch-1", "date": "2026-10-20"},
                                              {"title": "nav", "url": "/en-us/cards"}]}}}) + '</script></html>'
    src = {"id": "riftbound_news", "game": "riftbound", "base": "https://riftbound.leagueoflegends.com", "parser": "nextjs", "path_prefix": "/en-us/news/"}
    items = parse(src, page)
    assert [i["url"] for i in items] == ["https://riftbound.leagueoflegends.com/en-us/news/radiance-is-here", "https://riftbound.leagueoflegends.com/en-us/news/game-updates/patch-1"]
    assert items[0]["date"] == "2026-10-23"


def test_anchors_parser_reads_riot_cards():
    from tcgwatcher.news import parse
    src = {"id": "riftbound_news", "game": "riftbound", "base": "https://playriftbound.com", "parser": "anchors", "path_prefix": "/en-us/news/"}
    html = '''<nav><a href="/en-us/news/">News</a></nav>
    <a class="card" href="/en-us/news/announcements/riftbounds-2026-drawings"><span>Announcements</span>
      <time datetime="2026-10-22T16:00:00.000Z">2026-10-22T16:00:00.000Z</time><h3>Riftbound&#x27;s 2026 Drawings</h3><p>K/DA Couture …</p></a>
    <a href="https://youtu.be/egXq3n8UHfE"><span>Announcements</span><time>2026-10-27T16:15:00Z</time><h3>Riftbound x K/DA: Behind the Scenes</h3></a>
    <a href="/en-us/game/"><h3>How to play</h3></a>'''
    items = parse(src, html)
    assert [i["title"] for i in items] == ["Riftbound's 2026 Drawings", "Riftbound x K/DA: Behind the Scenes"]
    assert items[0]["url"] == "https://playriftbound.com/en-us/news/announcements/riftbounds-2026-drawings" and items[0]["date"] == "2026-10-22"


def test_old_items_are_marked_seen_but_not_alerted(tmp_path, monkeypatch):
    import tcgwatcher.news as news, tcgwatcher.store as store
    monkeypatch.setattr(store, "DATA", tmp_path)
    src = {"id": "x", "game": "riftbound", "name": "X", "base": "https://a.b", "parser": "anchors", "path_prefix": "/news/", "kind": "all", "url": "https://a.b/news"}
    monkeypatch.setattr(news, "load_config", lambda: {"sources": [src], "product": "x", "ignore": "zzz", "max_age_days": 14})
    page = ('<a href="/news/old"><time>2026-09-01T00:00:00Z</time><h3>Old thing</h3></a>'
            '<a href="/news/new"><time>2026-11-01T00:00:00Z</time><h3>New thing</h3></a>')
    class H:
        def get(self, u): return page, None
    store.write_json(store.state_path("news"), {"seen": {"x|https://a.b/news/seed": {}}})   # not the first run
    r = news.run(day="2026-11-03", http=H(), send=False)
    assert [m["key"] for m in r["messages"]] == ["x|https://a.b/news/new"]


def test_anchors_accepts_bandai_date_before_link():
    from tcgwatcher.news import parse
    src = {"id": "dbs_updates", "game": "dbs", "base": "https://www.dbs-cardgame.com", "parser": "anchors", "path_prefix": "/us-en/"}
    page = '<li><span class="date">2026/10/16</span><a href="/us-en/product/dbs-b32.php">PRODUCTS (-CHROMATIC ASCENSION- [DBS-B32]) info has been updated.</a></li>'
    it = parse(src, page)
    assert it and it[0]["date"] == "2026-10-16" and it[0]["url"].endswith("/us-en/product/dbs-b32.php") and "CHROMATIC" in it[0]["title"]


def test_youtube_feed_parser():
    from tcgwatcher.news import parse
    src = {"id": "yt", "game": "riftbound", "base": "https://www.youtube.com", "parser": "youtube"}
    page = '<html>… "channelId":"UCabcdefghijklmnopqrstuv" …</html>'
    feed = ('<feed><entry><title>Gift of the Rift reveal</title><link rel="alternate" href="https://www.youtube.com/watch?v=x1"/>'
            '<published>2026-10-22T16:00:00+00:00</published></entry></feed>')
    class H:
        def get(self, u):
            assert u.endswith("channel_id=UCabcdefghijklmnopqrstuv"); return feed, None
    it = parse(src, page, H())
    assert it == [{"source": "yt", "game": "riftbound", "url": "https://www.youtube.com/watch?v=x1", "title": "Gift of the Rift reveal", "date": "2026-10-22"}]


def test_watchdog_warns_weekly_on_broken_or_stale_source(tmp_path, monkeypatch):
    import tcgwatcher.news as news, tcgwatcher.store as store
    monkeypatch.setattr(store, "DATA", tmp_path)
    srcs = [{"id": "dead", "game": "dbs", "name": "Dead", "base": "https://a.b", "parser": "anchors", "url": "https://a.b/dead", "kind": "all"},
            {"id": "stale", "game": "dbs", "name": "Stale", "base": "https://a.b", "parser": "anchors", "url": "https://a.b/stale", "kind": "all"}]
    monkeypatch.setattr(news, "load_config", lambda: {"sources": srcs, "product": "x", "ignore": "zzz", "max_age_days": 14, "stale_days": 45})
    class H:
        def get(self, u):
            if u.endswith("dead"): return '<a href="/dead">News</a>', None            # only the index itself → nothing parsed
            return '<a href="/stale/old"><time>2026-06-01T00:00:00Z</time><h3>Months ago</h3></a>', None
    r = news.run(day="2026-11-03", http=H(), send=False)
    assert sorted(m["key"] for m in r["messages"]) == ["health|dead|2026-11-03", "health|stale|2026-11-03"]
    r = news.run(day="2026-11-05", http=H(), send=False)
    assert r["messages"] == []                                                        # warned this week already
    r = news.run(day="2026-11-11", http=H(), send=False)
    assert len(r["messages"]) == 2
