"""Retailer monitor tests with a fake HTTP layer (no network)."""
import json
from tcgwatcher import retail, store


class FakeHttp:
    def __init__(self, pages): self.pages = pages
    def get(self, url, json_=False):
        for k, v in self.pages.items():
            if url.startswith(k):
                return (v if json_ else v), None
        return None, "http 404"


SHOPIFY = {"products": [
    {"title": "Dragon Ball Super Card Game - Fusion World - FB11 - Display [ENG] Vorbestellung", "handle": "fb11-display", "tags": [],
     "variants": [{"price": "124.99", "available": True}]},
    {"title": "Dragon Ball Super Card Game - Fusion World - FB10 - Display [ENG]", "handle": "fb10-display", "tags": [],
     "variants": [{"price": "129.99", "available": False}]},
    {"title": "Pokemon Scarlet Violet Display", "handle": "pkm", "tags": [], "variants": [{"price": "150", "available": True}]},
]}
WOO = [{"name": "Dragon Ball - Fusion World Booster FB11", "permalink": "https://dracoon.nl/product/fb11/",
        "prices": {"price": "13499", "currency_minor_unit": 2}, "is_in_stock": True, "is_purchasable": True, "short_description": "Pre-Order: dit product nu"},
       {"name": "Dragon Ball - Fusion World Booster FB10", "permalink": "https://dracoon.nl/product/fb10/",
        "prices": {"price": "11999", "currency_minor_unit": 2}, "is_in_stock": False, "is_purchasable": True, "short_description": ""}]
CAT_HTML = '<a href="/a-1/dragon-ball-tcg/pre-order-fb11-booster-box/">(Pre-order) Fusion World FB11 Booster Box</a><a href="/c-1/pokemon/">Pokemon</a>'
PROD_HTML = '<html><head><title>FB11 Booster Box - TBH</title><meta property="product:price:amount" content="129.95"></head><body>Pre-order September 2026 <button>In winkelwagen</button></body></html>'


def fake_http():
    return FakeHttp({
        "https://fantasiacards.de/en/collections/dragon-ball/Booster-Display/products.json": SHOPIFY,
        "https://dracoon.nl/wp-json/wc/store/v1/products?search=fusion": WOO,
        "https://dracoon.nl/wp-json/wc/store/v1/products?search=": [],
        "https://www.tbhstore.nl/c-7780596/dragon-ball-tcg/": CAT_HTML,
        "https://www.tbhstore.nl/a-1/dragon-ball-tcg/pre-order-fb11-booster-box/": PROD_HTML,
    })


def test_extractors(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA", tmp_path)
    cfg = retail.load_retailers()
    by = {r["id"]: r for r in cfg["retailers"]}
    rows, err = retail.shopify_collection(fake_http(), by["fantasiacards"])
    assert err is None and len(rows) == 2
    assert {r["status"] for r in rows} == {"preorder", "sold_out"} and rows[0]["price"] == 124.99
    rows, err = retail.woo_store(fake_http(), by["dracoon"])
    assert [r["status"] for r in rows] == ["preorder", "sold_out"] and rows[0]["price"] == 134.99
    rows, err = retail.generic_category(fake_http(), by["tbhstore"])
    assert len(rows) == 1
    row = retail.generic_product(fake_http(), rows[0])
    assert row["price"] == 129.95 and row["status"] == "preorder"


def test_run_and_transitions(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA", tmp_path)
    r = retail.run(day="2026-10-17", http=fake_http(), retailer_ids=["fantasiacards", "dracoon", "tbhstore"])
    assert r["rows"] == 5 and all(t["kind"] == "new_listing" for t in r["transitions"])
    assert retail.format_alerts(r["transitions"], first_run=True)[0].startswith("🏪")
    # second observation: FB10 at fantasia back in stock, dracoon FB11 price drop
    SHOPIFY["products"][1]["variants"][0]["available"] = True
    WOO[0]["prices"]["price"] = "11999"
    r2 = retail.run(day="2026-10-17", http=fake_http(), retailer_ids=["fantasiacards", "dracoon", "tbhstore"])
    kinds = sorted(t["kind"] for t in r2["transitions"])
    assert kinds == ["price_drop", "sold_out->in_stock"]
    msgs = retail.format_alerts(r2["transitions"], first_run=False)
    assert any("terug op voorraad" in m for m in msgs) and any("prijs omlaag" in m for m in msgs)
    day = json.load(open(tmp_path / "retail" / "2026-10-17.json"))
    assert len(day["results"]) == 10   # append-only within the day
