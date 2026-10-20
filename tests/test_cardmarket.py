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
    assert box_url("Awakened Pulse Booster Box [Fusion World]").endswith("/Booster-Boxes/Awakened-Pulse-Booster-Box-Fusion-World?language=1")
    assert box_url("Saiyan's Pride Booster Box [Fusion World]").endswith("/Saiyans-Pride-Booster-Box-Fusion-World?language=1")


def test_card_url_matches_browser():
    from tcgwatcher.cardmarket import card_url
    u = card_url("Saiyan's Pride [Fusion World]", "Son Goten (FB08-121) [Fusion World]", 3, "SAA", "Secret Rare", 3)
    assert u == "https://www.cardmarket.com/en/DragonBallSuper/Products/Singles/Saiyans-Pride-Fusion-World/Son-Goten-FB08-121-Fusion-World-V3-God-Rare?language=1"
    u = card_url("Saiyan's Pride [Fusion World]", "Broly // Broly (FB08-073) [Fusion World]", 2, "AA", "Super Rare", 2)
    assert u.endswith("/Saiyans-Pride-Fusion-World/Broly-Broly-FB08-073-Fusion-World-V2-Alternate-Art?language=1")
    # base printing of a multi-printing card carries V1 + its rarity; a single-printing card has no suffix
    assert card_url("Saiyan's Pride [Fusion World]", "Son Goten (FB08-121) [Fusion World]", 1, "", "Secret Rare", 3).endswith("/Son-Goten-FB08-121-Fusion-World-V1-Secret-Rare?language=1")
    assert card_url("Rivals Clash", "Broly : BR (FB06-120) [Fusion World]", 1, "", "Secret Rare", 2).endswith("/Rivals-Clash/Broly-BR-FB06-120-Fusion-World-V1-Secret-Rare?language=1")
    assert card_url("Wish for Shenron [Fusion World]", "Son Goten/Trunks : Youth (FB07-105) [Fusion World]", 2, "AA", "Super Rare", 2).endswith("/Wish-for-Shenron-Fusion-World/Son-GotenTrunks-Youth-FB07-105-Fusion-World-V2-Alternate-Art?language=1")
    assert card_url("Awakened Pulse [Fusion World]", "Krillin (FB01-010) [Fusion World]", 1, "", "Common", 1).endswith("/Awakened-Pulse-Fusion-World/Krillin-FB01-010-Fusion-World?language=1")


def test_expansion_names():
    from tcgwatcher.cardmarket import expansion_name, card_url
    assert expansion_name("dbs", "BT21", "Wild Resurgence") == "Zenkai Series Set 04"
    assert expansion_name("dbfw", "FB08", "Saiyan's Pride", "Saiyan's Pride Booster Box [Fusion World]") == "Saiyan's Pride [Fusion World]"
    assert expansion_name("dbfw", "FB06", "Rivals Clash", "Rivals Clash Booster Box [Fusion World]") == "Rivals Clash"
    assert expansion_name("dbfw", "FB03", "Raging Roar", "Raging Roar Booster Box [Fusion World]") == "Raging Roar"
    assert card_url(expansion_name("dbfw", "FB04", "Ultra Limit", "Ultra Limit Booster Box [Fusion World]"), "Son Goku (FB04-129) [Fusion World]", 3, "SAA", "Secret Rare", 3).endswith("/Ultra-Limit/Son-Goku-FB04-129-Fusion-World-V3-God-Rare?language=1")
    assert card_url("Zenkai Series Set 04", "Son Goku, Overwhelming Power", 2, "SPR", "Super Rare", 2).endswith("/Zenkai-Series-Set-04/Son-Goku-Overwhelming-Power-V2-Special-Rare?language=1")
    assert card_url("Manga Booster 01 [Fusion World]", "Vegeta // Vegeta (FB04-051) [Fusion World]", 2, "GOLD", "Leader", 2).endswith("/Manga-Booster-01-Fusion-World/Vegeta-Vegeta-FB04-051-Fusion-World-V2-Alternate-Art?language=1")


def test_join_prefers_the_sets_own_expansion():
    """FB03-139 exists twice on Cardmarket (two expansions); the cheaper one lives elsewhere and its URL would be wrong."""
    f = files()
    f["prices"]["priceGuides"] += [{"idProduct": 783017, "low": 80.0, "avg30": 90.0}, {"idProduct": 790367, "low": 60.0, "avg30": 70.0}]
    f["singles"]["products"] = [{"idProduct": 783016, "name": "Son Gohan : Youth (FB03-139) [Fusion World]", "idExpansion": 10},
                                {"idProduct": 783017, "name": "Son Gohan : Youth (FB03-139) [Fusion World]", "idExpansion": 10},
                                {"idProduct": 790366, "name": "Son Gohan : Youth (FB03-139) [Fusion World]", "idExpansion": 11},
                                {"idProduct": 790367, "name": "Son Gohan : Youth (FB03-139) [Fusion World]", "idExpansion": 11}]
    f["nonsingles"]["products"].append({"idProduct": 7101, "name": "Raging Roar Booster Box [Fusion World]", "idExpansion": 10})
    f["prices"]["priceGuides"].append({"idProduct": 7101, "low": 85.0})
    latest = {"games": {"dbfw": {"cards": [{"key": "dbfw:FB03:FB03-139:AA", "product_id": 600}], "sets": [{"code": "FB03", "name": "Raging Roar"}]}}}
    ct = {"blueprints": {"1": {"tcg_player_id": 600, "card_market_ids": [783017, 790367]}}}
    cards, boxes = cardmarket.join(f, latest, ct)
    c = cards["dbfw:FB03:FB03-139:AA"]
    assert c["idProduct"] == 783017 and c["low"] == 80.0 and c["version"] == 2
    assert c["url"].endswith("/Raging-Roar/Son-Gohan-Youth-FB03-139-Fusion-World-V2-Alternate-Art?language=1")


def test_box_name_match_ignores_plurals():
    from tcgwatcher.cardmarket import _name_match
    assert _name_match("Beyond Generations", "Beyond Generation Booster Box")
    assert _name_match("Legend of the Dragon Balls", "Legends of the Dragon Balls Booster Box")
    assert _name_match("Perfect Combination", "Perfect Combination Booster Box")
    assert not _name_match("Wild Resurgence", "Critical Blow Booster Box")


def test_fusion_world_expansion_names_all_verified():
    """Every FW set's English expansion name as clicked in the browser (2026-10-19/20)."""
    from tcgwatcher.cardmarket import expansion_name
    box = lambda n: f"{n} Booster Box [Fusion World]"  # noqa: E731
    assert expansion_name("dbfw", "FB01", "Awakened Pulse", box("Awakened Pulse")) == "Awakened Pulse [Fusion World]"
    assert expansion_name("dbfw", "FB02", "Blazing Aura", box("Blazing Aura")) == "Blazing Aura [Fusion World]"
    assert expansion_name("dbfw", "FB03", "Raging Roar", box("Raging Roar")) == "Raging Roar"
    assert expansion_name("dbfw", "FB04", "Ultra Limit", box("Ultra Limit")) == "Ultra Limit"
    assert expansion_name("dbfw", "FB05", "New Adventure", box("New Adventure")) == "New Adventure [Fusion World]"
    assert expansion_name("dbfw", "FB06", "Rivals Clash", box("Rivals Clash")) == "Rivals Clash"
    assert expansion_name("dbfw", "FB07", "Wish for Shenron", box("Wish for Shenron")) == "Wish for Shenron [Fusion World]"
    assert expansion_name("dbfw", "FB08", "Saiyan's Pride", box("Saiyan's Pride")) == "Saiyan's Pride [Fusion World]"
    assert expansion_name("dbfw", "FB10", "Cross Force", box("Cross Force")) == "Cross Force [Fusion World]"
    assert expansion_name("dbfw", "SB01", "Manga Booster 01", box("Manga Booster 01")) == "Manga Booster 01 [Fusion World]"
