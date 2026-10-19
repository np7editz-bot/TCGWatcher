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
        "https://fantasiacards.de/en/collections/dragon-ball/products.json": SHOPIFY,
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
    assert retail.format_alerts(r["transitions"], first_run=True)[0]["text"].startswith("🏪")
    # second observation: FB10 at fantasia back in stock, dracoon FB11 price drop
    SHOPIFY["products"][1]["variants"][0]["available"] = True
    WOO[0]["prices"]["price"] = "11999"
    r2 = retail.run(day="2026-10-17", http=fake_http(), retailer_ids=["fantasiacards", "dracoon", "tbhstore"])
    kinds = sorted(t["kind"] for t in r2["transitions"])
    assert kinds == ["price_drop", "sold_out->in_stock"]
    # v3: without a valuation (set unknown) only "becomes buyable" is actionable; a price drop on an unscored set is not
    msgs = retail.format_alerts(r2["transitions"], first_run=False)
    assert [m["text"].split(" ·")[0] for m in msgs] == ["✅ OP VOORRAAD"]
    # with a valuation: FB10 hits €104 vs €X → gated on buy.near; FB11 price drop to €119.99 with hits €160 → ratio 1.33 → message
    latest = {"games": {"dbfw": {"sets": [{"code": "FB10", "ev_eu_excl_saa": 40.0, "flags": []},
                                           {"code": "FB11", "ev_eu_excl_saa": 160.0, "flags": []}]}}}
    msgs = retail.format_alerts(r2["transitions"], first_run=False, latest=latest, buy={"threshold": 0.8, "near": 0.7})
    assert [m["text"].split(" ·")[0] for m in msgs] == ["📉 GOEDKOPER"] and "koopwaardig" in msgs[0]["text"]
    day = json.load(open(tmp_path / "retail" / "2026-10-17.json"))
    assert len(day["results"]) == 10   # append-only within the day


def test_buy_button_overrides_structured_availability():
    """Ony TCG keeps schema.org PreOrder on a page whose buy button is disabled 'Out of stock'."""
    page = ('<html><head><title>Box FB-11</title><meta property="product:price:amount" content="119.99">'
            '<script type="application/ld+json">{"availability":"https://schema.org/PreOrder"}</script></head>'
            '<body><button class="btn product-detail__cart-btn" disabled>Out of stock</button></body></html>')
    class H:
        def get(self, url, json_=False):
            return page, None
    row = retail.generic_product(H(), {"url": "https://onytcg.com/en/product/box-fb-11", "title": "Box FB-11"})
    assert row["status"] == "sold_out" and row["price"] == 119.99
    ok = page.replace(' disabled>Out of stock', '>Add to cart')
    class H2:
        def get(self, url, json_=False):
            return ok, None
    assert retail.generic_product(H2(), {"url": "https://onytcg.com/en/product/box-fb-11", "title": "Box FB-11"})["status"] == "preorder"


def test_status_text_prefers_product_over_related_items():
    """Comic Planet SB02: 'Nicht mehr verfügbar' + notify form, while related products below say 'In den Warenkorb'."""
    page = ("Regulärer Preis: 129,99 € Nicht mehr verfügbar Benachrichtige mich, sobald der Artikel lieferbar ist. "
            "Kunden haben sich ebenfalls angesehen ... Vorbestellung ... In den Warenkorb")
    assert retail.status_from_text(page) == "sold_out"
    assert retail.status_from_text("129,99 € In den Warenkorb ... andere Artikel: ausverkauft") == "in_stock"
    assert retail.status_from_text("Vorbestellung möglich, Lieferung ab 12.03.2027. In den Warenkorb") == "preorder"


def test_only_api_trusted_shops_alert(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA", tmp_path)
    r = retail.run(day="2026-10-17", http=fake_http(), retailer_ids=["fantasiacards", "dracoon", "tbhstore"])
    by = {t["retailer"]: t for t in r["transitions"]}
    assert by["fantasiacards"]["trusted"] and by["dracoon"]["trusted"]
    assert by["tbhstore"]["trusted"] is False   # generic HTML shop: data only
    t = [dict(x, kind="sold_out->preorder", status="preorder", trusted=False, product_kind="box", price=99.0) for x in r["transitions"]][:1]
    assert retail.format_alerts(t, first_run=False) == []
    t[0]["trusted"] = True
    assert len(retail.format_alerts(t, first_run=False)) == 1
