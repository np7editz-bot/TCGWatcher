from tcgwatcher import ebay, store


def item(title, value, cur="EUR", ship=4.5, iid=None, mp="EBAY_DE", loc="DE"):
    return {"title": title, "itemId": iid or title, "price": {"value": str(value), "currency": cur}, "itemWebUrl": "https://ebay.de/itm/1",
            "shippingOptions": [{"shippingCost": {"value": str(ship), "currency": cur}}], "listingMarketplaceId": mp, "itemLocation": {"country": loc}}


class FakeClient:
    configured = True
    calls = 0
    def search(self, q, marketplace="EBAY_DE", limit=50, sleep=0):
        self.calls += 1
        if "FB09-123" in q:
            return [item("Gogeta GT FB09-123 SAA PSA 10 Dragon Ball Fusion World", 1500), item("Gogeta GT FB09-123 Super Alt Art PSA 9 GEM", 1200),
                    item("Gogeta GT FB09-123 Japanese SAA PSA 10", 500), item("Gogeta GT FB09-123 SAA raw NM", 1300),
                    item("Gogeta GT FB09-123 SCR PSA 10", 120, iid="base"),                       # base secret rare, same number
                    item("Gogeta GT FB09-123 God Rare PSA 10", 1400, iid="us", loc="US")]         # non-EU: counts for selling, not for buying
        return [item("Son Goku FB09-025 God Rare PSA 10", 1100, iid="x"), item("Son Goku FB09-025 SAA BGS 9.5", 900, iid="y")]


def latest():
    return {"fx": {"usd_eur": 0.9}, "games": {"dbfw": {"cards": [
        {"key": "dbfw:FB09:FB09-123:SAA", "set": "FB09", "number": "FB09-123", "name": "Gogeta : GT - FB09-123 (Super Alternate Art)",
         "class": "SAA", "variant": "SAA", "price": 2000.0, "price_src": "market", "image": None},
        {"key": "dbfw:FB09:FB09-025:SAA", "set": "FB09", "number": "FB09-025", "name": "Son Goku - FB09-025 (Super Alternate Art)",
         "class": "SAA", "variant": "SAA", "price": 1300.0, "price_src": "market", "image": None},
        {"key": "dbfw:FB09:FB09-001:", "set": "FB09", "number": "FB09-001", "name": "Gogeta", "class": "SCR", "price": 5.0, "price_src": "market"}]}}}


def test_parse_listing_filters_grade_number_japanese_promo_and_variant():
    fx = 0.9
    assert ebay.parse_listing(item("Gogeta GT FB09-123 PSA 10", 1500), "FB09-123", fx)["price"] == 1504.5          # base variant
    assert ebay.parse_listing(item("Gogeta GT FB09-123 raw", 1500), "FB09-123", fx) is None
    assert ebay.parse_listing(item("Gogeta GT FB09-123 Japanese PSA 10", 500), "FB09-123", fx) is None
    assert ebay.parse_listing(item("Gogeta GT FB09-121 PSA 10", 500), "FB09-123", fx) is None
    assert ebay.parse_listing(item("Gogeta FB09-123 BGS 9.5", 1000, cur="USD", ship=10), "FB09-123", fx)["price"] == 909.0
    # same number, different card: SCR / Alternate Art / Super Alternate Art, and tournament promos
    assert ebay.parse_listing(item("Son Goku FB01-139 SCR PSA 10", 150), "FB01-139", fx, "SAA") is None
    assert ebay.parse_listing(item("Son Goku FB01-139 Secret Rare Alternate Art PSA 10", 150), "FB01-139", fx, "AA")["price"] == 154.5
    assert ebay.parse_listing(item("Son Goku FB01-139 Secret Rare Alternate Art PSA 10", 150), "FB01-139", fx, "") is None
    assert ebay.parse_listing(item("Son Goku FB01-139 SAA God Rare PSA 10", 1500), "FB01-139", fx, "SAA")["price"] == 1504.5
    assert ebay.parse_listing(item("Son Goku FB01-139 SAA God Rare PSA 10", 1500), "FB01-139", fx, "AA") is None
    assert ebay.parse_listing(item("PSA 10 Son Goku SR Star FB05-030 Ultimate Battle Winner", 895), "FB05-030", fx, "AA") is None


def test_fetch_day_and_slab_signal(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA", tmp_path)
    L = latest()
    t = ebay.targets(L)
    assert [x["number"] for x in t] == ["FB09-123", "FB09-025"] and t[0]["name"] == "Gogeta : GT" and t[0]["raw_eur"] == 1800.0
    doc = ebay.fetch_day(client=FakeClient(), latest=L, marketplaces=("EBAY_DE",), day="2026-10-19")
    g = doc["cards"]["dbfw:FB09:FB09-123:SAA"]["best"]
    assert g["10.0"]["n"] == 2 and g["10.0"]["eu_low"] == 1504.5 and g["10.0"]["eu_n"] == 1 and g["9.0"]["low"] == 1204.5 and "9.5" not in g
    sig = ebay.slab_signals(doc, {"max_vs_raw": 1.15, "min_raw_eur": 50, "grades": [10, 9.5, 9]})
    # Gogeta PSA10 €1504 vs raw €1800 → 0.84 ✔ ; Goku PSA10 €1104.5 vs raw €1170 → 0.94 ✔ (sorted by ratio)
    assert [(s["number"], s["grade"], s["ratio"]) for s in sig] == [("FB09-123", 10, 0.84), ("FB09-025", 10, 0.94)]
    assert ebay.latest_day()["day"] == "2026-10-19"


def test_grade_signal_cheap_raw_expensive_psa10(tmp_path, monkeypatch):
    """Raw €80, PSA10 median €400 (4 listings), PSA9 €150 → expected 0.4×400+0.6×150=250 → net 250×0.87−25−80=112.5 ✔."""
    psa = {"10.0": {"low": 350.0, "median": 400.0, "n": 4, "url": "u"}, "9.0": {"low": 120.0, "median": 150.0, "n": 2, "url": "u9"}}
    bgs = {"10.0": {"low": 900.0, "median": 1000.0, "n": 3, "url": "b10"}, "9.5": {"low": 380.0, "median": 420.0, "n": 5, "url": "b95"},
           "9.0": {"low": 120.0, "median": 140.0, "n": 2, "url": "b9"}}
    doc = {"cards": {"k": {"number": "FB10-097", "name": "Cell", "set": "FB10", "class": "SR_AA", "raw_eur": 80.0, "image": None,
                           "best": {}, "by_grader": {"PSA": psa, "BGS": bgs}},
                     "thin": {"number": "FB10-001", "name": "Goku", "set": "FB10", "class": "SR_AA", "raw_eur": 80.0, "best": {},
                              "by_grader": {"PSA": {"10.0": {"low": 350.0, "median": 400.0, "n": 2, "url": "u"}}}}}}
    cfg = {"fee_pct": 13, "min_top_n": 3, "min_profit_eur": 50, "min_roi": 0.5,
           "services": {"PSA": {"cost_eur": 25, "mix": {"10": 0.4, "9": 0.6}},
                        "BGS": {"cost_eur": 30, "mix": {"10": 0.1, "9.5": 0.45, "9": 0.45}}}}
    sig = ebay.grade_signals(doc, cfg)
    # BGS: 0.1×1000 + 0.45×420 + 0.45×140 = 352 → 352×0.87 − 30 − 80 = 196.24 ; PSA: 250×0.87 − 25 − 80 = 112.5 → BGS wins
    assert [s["number"] for s in sig] == ["FB10-097"] and sig[0]["service"] == "BGS" and sig[0]["net"] == 196.24
    assert [o["service"] for o in sig[0]["options"]] == ["BGS", "PSA"] and sig[0]["options"][1]["net"] == 112.5
    assert ebay.grade_signals(doc, dict(cfg, min_profit_eur=300)) == []


def test_cardtrader_graded_listings_join_ebay():
    ct = {"graded": {"375875": {"set": "FB09", "listings": [
        {"price": 1400.0, "zero": True, "country": "IT", "id": 1, "props": {"condition": "Mint", "graded": True, "grading_company": "PSA", "grading_score": "10"}, "description": ""},
        {"price": 900.0, "zero": False, "country": "DE", "id": 2, "props": {"condition": "Near Mint"}, "description": "BGS 9.5 Gem Mint slab"},
        {"price": 500.0, "zero": True, "country": "ES", "id": 3, "props": {"condition": "Mint"}, "description": "graded, no grade stated"}]}}}
    out = ebay.cardtrader_slabs(ct, 375875)
    assert [(l["grader"], l["grade"], l["price"], l["eu"]) for l in out] == [("PSA", 10.0, 1406.0, True), ("BGS", 9.5, 905.0, True)]
    assert out[0]["url"] == "https://www.cardtrader.com/cards/375875" and out[0]["marketplace"] == "CARDTRADER"
