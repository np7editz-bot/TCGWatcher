from tcgwatcher import ebay, store


def item(title, value, cur="EUR", ship=4.5, iid=None, mp="EBAY_DE"):
    return {"title": title, "itemId": iid or title, "price": {"value": str(value), "currency": cur}, "itemWebUrl": "https://ebay.de/itm/1",
            "shippingOptions": [{"shippingCost": {"value": str(ship), "currency": cur}}], "listingMarketplaceId": mp, "itemLocation": {"country": "DE"}}


class FakeClient:
    configured = True
    calls = 0
    def search(self, q, marketplace="EBAY_DE", limit=50, sleep=0):
        self.calls += 1
        if "FB09-123" in q:
            return [item("Gogeta GT FB09-123 PSA 10 Dragon Ball Fusion World", 1500), item("Gogeta GT FB09-123 PSA 9 GEM", 1200),
                    item("Gogeta GT FB09-123 Japanese PSA 10", 500), item("Gogeta GT FB09-123 raw NM", 1300)]
        return [item("Son Goku FB09-025 PSA 10", 1100, iid="x"), item("Son Goku FB09-025 BGS 9.5", 900, iid="y")]


def latest():
    return {"fx": {"usd_eur": 0.9}, "games": {"dbfw": {"cards": [
        {"key": "dbfw:FB09:FB09-123:SAA", "set": "FB09", "number": "FB09-123", "name": "Gogeta : GT - FB09-123 (Super Alternate Art)",
         "class": "SAA", "price": 2000.0, "price_src": "market", "image": None},
        {"key": "dbfw:FB09:FB09-025:SAA", "set": "FB09", "number": "FB09-025", "name": "Son Goku - FB09-025 (Super Alternate Art)",
         "class": "SAA", "price": 1300.0, "price_src": "market", "image": None},
        {"key": "dbfw:FB09:FB09-001:", "set": "FB09", "number": "FB09-001", "name": "Gogeta", "class": "SCR", "price": 5.0, "price_src": "market"}]}}}


def test_parse_listing_filters_grade_number_and_japanese():
    fx = 0.9
    assert ebay.parse_listing(item("Gogeta GT FB09-123 PSA 10", 1500), "FB09-123", fx)["price"] == 1504.5
    assert ebay.parse_listing(item("Gogeta GT FB09-123 raw", 1500), "FB09-123", fx) is None
    assert ebay.parse_listing(item("Gogeta GT FB09-123 Japanese PSA 10", 500), "FB09-123", fx) is None
    assert ebay.parse_listing(item("Gogeta GT FB09-121 PSA 10", 500), "FB09-123", fx) is None
    assert ebay.parse_listing(item("Gogeta FB09-123 BGS 9.5", 1000, cur="USD", ship=10), "FB09-123", fx)["price"] == 909.0


def test_fetch_day_and_slab_signal(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA", tmp_path)
    L = latest()
    t = ebay.targets(L)
    assert [x["number"] for x in t] == ["FB09-123", "FB09-025"] and t[0]["name"] == "Gogeta : GT" and t[0]["raw_eur"] == 1800.0
    doc = ebay.fetch_day(client=FakeClient(), latest=L, marketplaces=("EBAY_DE",), day="2026-10-19")
    g = doc["cards"]["dbfw:FB09:FB09-123:SAA"]["best"]
    assert g["10.0"]["low"] == 1504.5 and g["9.0"]["low"] == 1204.5 and "9.5" not in g
    sig = ebay.slab_signals(doc, {"max_vs_raw": 1.15, "min_raw_eur": 50, "grades": [10, 9.5, 9]})
    # Gogeta PSA10 €1504 vs raw €1800 → 0.84 ✔ ; Goku PSA10 €1104.5 vs raw €1170 → 0.94 ✔ (sorted by ratio)
    assert [(s["number"], s["grade"], s["ratio"]) for s in sig] == [("FB09-123", 10, 0.84), ("FB09-025", 10, 0.94)]
    assert ebay.latest_day()["day"] == "2026-10-19"
