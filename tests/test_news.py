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
    assert first["items"] == 5 and first["messages"] == []          # first run: learn, don't spam
    second = news.run(http=FakeHttp(pages()), day="2026-10-20", send=False)
    texts = [m["text"] for m in second["messages"]]
    assert len(texts) == 1 and "REACH THE GOD" in texts[0] and "Fusion World" in texts[0]
    # digital-app notice and events are stored, not sent; nothing repeats
    assert news.run(http=FakeHttp(pages()), day="2026-10-21", send=False)["messages"] == []
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
