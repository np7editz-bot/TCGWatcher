from tcgwatcher import cardmarket, store


def files():
    return {"prices": {"createdAt": "2026-10-19T02:43:15+0200", "priceGuides": [
                {"idProduct": 901, "low": 1300.0, "trend": 1500.0, "avg1": None, "avg7": 1700.0, "avg30": 2142.86},
                {"idProduct": 902, "low": 150.0, "trend": 197.0, "avg7": 193.0, "avg30": 157.78},
                {"idProduct": 7001, "low": 159.0, "trend": 260.16, "avg7": 239.61, "avg30": 259.85},
                {"idProduct": 7002, "low": 89.97, "trend": 100.0, "avg30": 95.0},
                {"idProduct": 7003, "low": 2900.0, "trend": 3000.0, "avg30": 2950.0}]},
            "singles": {"products": []},
            "nonsingles": {"products": [
                {"idProduct": 7001, "name": "Dual Evolution Booster Box [Fusion World]", "idCategory": 1, "categoryName": "Dragon Ball Super Booster Boxes"},
                {"idProduct": 7002, "name": "Dual Evolution Booster Box [Fusion World] (Asia Region Legal)", "idCategory": 1, "categoryName": "x"},
                {"idProduct": 7003, "name": "Dual Evolution Booster Box Case [Fusion World]", "idCategory": 1, "categoryName": "x"}]}}


def test_join_cards_via_cardtrader_ids_and_boxes_by_name():
    latest = {"games": {"dbfw": {"cards": [{"key": "dbfw:FB09:FB09-123:SAA", "product_id": 500}, {"key": "dbfw:FB09:FB09-001:AA", "product_id": 501},
                                           {"key": "dbfw:FB09:FB09-050:", "product_id": 502}],
                                 "sets": [{"code": "FB09", "name": "Dual Evolution"}]}}}
    ct = {"blueprints": {"1": {"tcg_player_id": 500, "card_market_ids": [901]}, "2": {"tcg_player_id": 501, "card_market_ids": [902, 999]},
                         "3": {"tcg_player_id": 502, "card_market_ids": []}}}
    cards, boxes = cardmarket.join(files(), latest, ct)
    assert cards["dbfw:FB09:FB09-123:SAA"]["avg30"] == 2142.86 and cards["dbfw:FB09:FB09-001:AA"]["low"] == 150.0
    assert "dbfw:FB09:FB09-050:" not in cards
    assert boxes["FB09"]["low"] == 159.0 and boxes["FB09"]["avg30"] == 259.85 and "Asia" not in boxes["FB09"]["name"] and "Case" not in boxes["FB09"]["name"]


def test_fetch_day_writes_once(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA", tmp_path)
    monkeypatch.setattr(cardmarket, "fetch_files", lambda game_id=13, session=None: files())
    latest = {"games": {"dbfw": {"cards": [], "sets": [{"code": "FB09", "name": "Dual Evolution"}]}}}
    d = cardmarket.fetch_day(latest=latest, ct_doc={"blueprints": {}}, day="2026-10-19")
    assert d["boxes"]["FB09"]["low"] == 159.0 and (tmp_path / "cardmarket" / "2026-10-19.json").exists()
    assert cardmarket.latest_day()["day"] == "2026-10-19"


def test_foil_fallback():
    from tcgwatcher.cardmarket import _f
    r = {"low": 10, "trend": 0, "avg30": None, "low-foil": 12.5, "trend-foil": 14.0, "avg30-foil": 13.2}
    assert _f(r, "low") == 10 and _f(r, "trend") == 14.0 and _f(r, "avg30") == 13.2
    assert _f({"avg7": 3.0, "avg7-foil": 9.0}, "avg7") == 3.0


def test_box_url_slug():
    from tcgwatcher.cardmarket import box_url
    assert box_url("Awakened Pulse Booster Box [Fusion World]").endswith("/Booster-Boxes/Awakened-Pulse-Booster-Box-Fusion-World")
    assert box_url("Saiyan's Pride Booster Box [Fusion World]").endswith("/Saiyans-Pride-Booster-Box-Fusion-World")
