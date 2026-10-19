"""Tests run against the committed seed data (data/) — real TCGplayer names and prices from 2026-10-15."""
import json
import pytest
from tcgwatcher import store, SITE
from tcgwatcher.config import load_games, group_kind
from tcgwatcher.model import classify, variant_of, sealed_kind_of, pick_price, load_game
from tcgwatcher.chase import score_sets
from tcgwatcher.history import change_pct
from tcgwatcher.validate import validate

DAY = "2026-10-15"


@pytest.fixture(scope="module")
def dbfw():
    return load_games()["dbfw"]


@pytest.mark.parametrize("name,rarity,cls,var", [
    ("Vegito - FB10-121 (Super Alternate Art)", "Secret Rare", "SAA", "SAA"),
    ("Gogeta : BR - FB09-001 (Super Alternate Art)", "Leader", "SAA", "SAA"),       # leader super alt-art counts as ★★
    ("Gogeta : BR - FB09-121 (Alternate Art)", "Secret Rare", "SCR_AA", "AA"),
    ("Gogeta : BR - FB09-121", "Secret Rare", "SCR", ""),
    ("Cell - SB01-001 (Gold)", "Leader", "SCR_AA", "GOLD"),
    ("Son Goku - FB10-011 (Alternate Art)", "Super Rare", "SR_AA", "AA"),
    ("Son Goku - FB10-011", "Super Rare", "SR", ""),
    ("Bulma (Alternate Art)", "Leader", "OTHER_AA", "AA"),
    ("Golden Frieza : BR (Alternate Art)", "Uncommon", "OTHER_AA", "AA"),
    ("Golden Frieza : BR", "Uncommon", None, ""),                                    # 'Golden' is not '(Gold)'
    ("Krillin (Dragon Ball Pattern)", "Common", None, "DBP"),                        # ST01 foil variant, not a hit
    ("Cross Force Booster Box", "", None, ""),
])
def test_classify_and_variant(dbfw, name, rarity, cls, var):
    assert classify(dbfw, name, rarity) == cls
    assert variant_of(dbfw, name) == var


@pytest.mark.parametrize("name,kind", [
    ("Cross Force Booster Box", "box"), ("Cross Force Booster Box Case", "case"), ("Cross Force Booster Pack", "pack"),
    ("Cross Force Sleeved Booster Pack [Cell]", None), ("Code Card - Cross Force Booster (FB10)", None),
])
def test_sealed_kind(dbfw, name, kind):
    assert sealed_kind_of(dbfw, name, "") == kind


def test_group_kind(dbfw):
    assert group_kind(dbfw, "FB10") == "booster"
    assert group_kind(dbfw, "SB02") == "booster"
    assert group_kind(dbfw, "ST01") == "booster"
    assert group_kind(dbfw, "FS13") == "starter"
    assert group_kind(dbfw, "FB11_PR") == "promo"
    assert group_kind(dbfw, "FB03_AR") == "promo"
    assert group_kind(dbfw, "DBSFW_TPR") == "promo"


def test_pick_price_prefers_market_then_mid_then_low():
    assert pick_price({"Holofoil": {"marketPrice": 10, "midPrice": 12, "lowPrice": 8}}) == (10, "market")
    assert pick_price({"Holofoil": {"marketPrice": None, "midPrice": 12, "lowPrice": 8}}) == (12, "mid")
    assert pick_price({"Holofoil": {"marketPrice": None, "midPrice": None, "lowPrice": 8}}) == (8, "low")
    assert pick_price({}) == (None, None)
    assert pick_price({"Normal": {"marketPrice": 0, "midPrice": None, "lowPrice": None}}) == (None, None)


def test_canonical_keys_and_composition(dbfw):
    m = load_game(dbfw, DAY)
    by_key = {p.key: p for p in m["products"]}
    assert "dbfw:FB10:FB10-121:SAA" in by_key
    assert "dbfw:FB10:sealed:box" in by_key
    scored = {s["code"]: s for s in score_sets(m)}
    fb07 = scored["FB07"]
    assert fb07["composition"]["SCR"]["n"] == 2 and fb07["composition"]["SCR_AA"]["n"] == 2 and fb07["composition"]["SAA"]["n"] == 1
    assert fb07["composition"]["SR_AA"]["n"] == 15
    # EV formula reproduced by hand for one class
    comp = fb07["composition"]["SCR_AA"]
    assert comp["ev"] == pytest.approx(0.15 * comp["avg"], abs=0.01)
    assert fb07["ratio_excl_saa"] == pytest.approx((fb07["ev"] - fb07["ev_saa"]) / fb07["box"]["price"], abs=0.001)
    # ST01 has 20 packs: estimates are scaled
    assert scored["ST01"]["composition"]["SR"]["est"] == pytest.approx(5.5 * 20 / 24, abs=0.001)
    assert "story" in scored["ST01"]["flags"]
    assert "presale" in scored["FB12"]["flags"]      # catalog is current state: FB11 lost presale on release day
    assert "no_cards" in scored["FB12"]["flags"]


def test_validation_clean_after_number_fixes():
    """TCGplayer's 'Piccolo - FB10-067' carries Number FB10-068 and FS03-16 has an anniversary reprint: both are corrected
    by config (number_fixes, ANN variant), so the known-source-error warnings are gone and the seed validates clean."""
    rep = validate(DAY)
    codes = {f["code"] for f in rep["games"]["dbfw"]["findings"]}
    assert "duplicate_keys" not in codes and "number_name_mismatch" not in codes
    assert rep["status"] in ("ok", "warn")      # seed day 10-15: FB11 presale coverage warning only


def test_change_pct_needs_enough_history():
    pts = [("2026-10-10", 100, "market"), ("2026-10-12", 110, "market"), ("2026-10-15", 121, "market")]
    assert change_pct(pts, "2026-10-15", 7) == pytest.approx(15.2, abs=0.1)
    assert change_pct(pts[-1:], "2026-10-15", 7) is None
    assert change_pct([("2026-10-10", 100, "low"), ("2026-10-12", 110, "low"), ("2026-10-15", 121, "market")], "2026-10-15", 7) is None


def test_latest_json_shape():
    latest = store.read_json(SITE / "data" / "latest.json")
    assert latest and "dbfw" in latest["games"] and "naruto" in latest["games"]
    g = latest["games"]["dbfw"]
    assert g["source"]["currency"] == "USD" and g["source"]["last_updated"]
    assert all({"code", "composition", "ev_excl_saa", "ratio_excl_saa", "flags"} <= set(s) for s in g["sets"])
    assert all({"key", "class", "price", "price_src", "product_id"} <= set(c) for c in g["cards"])


def test_real_day_2_fixes(dbfw):
    """Regression on the first real nightly snapshot (2026-10-16): promo groups never score, gold rule is leader-only,
    a fresh release with mostly listing prices is flagged low_coverage (and therefore excluded from ratio alerts)."""
    m = load_game(dbfw, "2026-10-16")
    promo_hits = [p for p in m["products"] if p.hit_class and m["sets"][p.set_code]["kind"] != "booster"]
    assert promo_hits == []
    assert classify(dbfw, "Energy Marker (E-42) (Gold)", "") is None
    scored = {s["code"]: s for s in score_sets(m)}
    assert "low_coverage" in scored["FB11"]["flags"]
    assert "low_coverage" not in scored["FB10"]["flags"]
    rep = validate("2026-10-16")
    dup = [f for f in rep["games"]["dbfw"]["findings"] if f["code"] == "duplicate_keys"]
    assert all(not k.split(":")[1].startswith("DBSFW") for f in dup for k in f["ids"])   # promos never counted as duplicates


def test_cardtrader_join_and_eu_ev():
    from tcgwatcher.cardtrader import eu_prices_by_tcgplayer
    from tcgwatcher.build import eu_ev
    doc = {"blueprints": {"1": {"name": "Vegito", "tcg_player_id": 683187, "set": "FB10"}, "2": {"name": "x", "tcg_player_id": None, "set": "FB10"}},
           "offers": {"1": {"set": "FB10", "low": 10.0, "median": 12.0, "n": 7}, "2": {"set": "FB10", "low": 1, "median": 1, "n": 1}}}
    eu = eu_prices_by_tcgplayer(doc)
    assert list(eu) == [683187] and eu[683187]["median"] == 12.0
    s = {"code": "FB10", "composition": {"SR": {"est": 5.5}, "SAA": {"est": 0.08}}}
    cards = [{"set": "FB10", "class": "SR", "price": 2.0, "eu": {"median": 3.0}}, {"set": "FB10", "class": "SR", "price": 4.0, "eu": None},
             {"set": "FB10", "class": "SAA", "price": 2000.0, "eu": None}]
    ev, covered, total = eu_ev(s, cards, 0.5)
    assert ev == pytest.approx(5.5 * (3.0 + 2.0) / 2, abs=0.01) and (covered, total) == (1, 2)


def test_buy_signal_uses_eu_ratio(tmp_path, monkeypatch):
    """FB09: US ratio 0.44 but EU box €181 (Zero) + €6 fee vs hits €173 → ratio 0.93 ≥ 0.80 → KOOP card; repeats only when ≥10% cheaper."""
    from tcgwatcher import alerts, build as b
    monkeypatch.setattr(store, "DATA", tmp_path)
    latest_path = tmp_path / "latest.json"
    monkeypatch.setattr(alerts, "LATEST", latest_path)
    hit = {"name": "Vegeta (Super Alternate Art)", "number": "FB09-001", "class": "SCR_AA", "price": 400.0, "image": None}
    s = {"code": "FB09", "name": "Dual Evolution", "flags": [], "ratio_excl_saa": 0.44, "ev_excl_saa": 190.0, "ev_eu_excl_saa": 173.0,
         "ratio_eu": 0.959, "box": {"price": 410.0, "product_id": 1, "name": "FB09 Booster Box", "image": None, "url": None}, "composition": {"SCR": {"n": 6}, "SCR_AA": {"n": 3}},
         "top_hit": hit, "hits_market_priced": 40, "hits_total": 42, "retail": []}
    cfg = {"zero_fee_eur": 6, "seller_shipping_eur": 15}
    eu = lambda low, zero: {"low": low, "n": 7, "zero_low": zero, "url": "https://www.cardtrader.com/cards/375875", **b.landed({"low": low, "zero_low": zero}, cfg)}  # noqa: E731
    s["best_buy"] = b.best_buy(dict(s, box={"price": 410.0, "product_id": 1, "name": "b", "eu": eu(170.0, 180.64)}))
    assert s["best_buy"]["ratio"] == pytest.approx(173 / 186.64, abs=0.001) and s["best_buy"]["source"] == "CardTrader Zero"
    assert b.landed({"low": 170.0, "zero_low": None}, cfg)["landed"] == 185.0     # no Zero offer: seller shipping estimate
    doc = {"fx": {"usd_eur": 0.91}, "games": {"dbfw": {"short": "FW", "sets": [s], "cards": [], "starters": [], "all_sets": []}}}
    store.write_json(latest_path, doc)
    monkeypatch.setattr(alerts, "load_games", lambda: {})
    msgs = alerts.run("2026-10-19", quality={"status": "ok", "games": {}}, send=False)
    buys = [m for m in msgs if m["kind"] == "buy"]
    assert len(buys) == 1 and "KOOP · FB09" in buys[0]["text"] and "CardTrader Zero" in buys[0]["text"] and "0.93" in buys[0]["text"]
    assert buys[0]["buttons"][0][0][1] == "https://www.cardtrader.com/cards/375875"
    assert not [m for m in msgs if m["kind"] == "briefing"]      # Monday, weekly briefing
    # same price next day → silent; 12% cheaper → "goedkoper geworden"
    assert not [m for m in alerts.run("2026-10-20", quality={"status": "ok", "games": {}}, send=False) if m["kind"] == "buy"]
    s["best_buy"] = b.best_buy(dict(s, box={"price": 410.0, "product_id": 1, "name": "b", "eu": eu(150.0, 158.0)}))
    store.write_json(latest_path, doc)
    again = [m for m in alerts.run("2026-10-21", quality={"status": "ok", "games": {}}, send=False) if m["kind"] == "buy"]
    assert len(again) == 1 and "goedkoper geworden" in again[0]["text"]


def test_number_fixes_and_anniversary_variant():
    from tcgwatcher.config import load_games
    from tcgwatcher.model import variant_of
    g = load_games()["dbfw"]
    assert g["number_fixes"][693131] == "FB10-067"
    assert variant_of(g, "Destructive Strength (1st Anniversary Set)") == "ANN"
    assert variant_of(g, "Piccolo - FB10-067 (Limited Pack 26-27 WAVE2)") == "LP"


def test_masters_promo_groups_and_variants():
    """TCGplayer reuses 'BT26' for the pre-release group; Masters parallels (SPR/SLR/SCR/GDR/Collector Booster) share numbers."""
    from tcgwatcher.config import load_games
    from tcgwatcher.model import variant_of, load_game
    from tcgwatcher.validate import validate
    g = load_games()["dbs"]
    assert variant_of(g, "Bulma, Opening Chapter (GDR)") == "GDR" and variant_of(g, "Spirit (SPR)") == "SPR"
    assert variant_of(g, "Son Goten, Joining the Party (Collector Booster Silver)") == "CBS"
    m = load_game(g, "2026-10-18")
    assert m["sets"]["BT26"]["kind"] == "booster" and m["sets"]["BT26_PR"]["kind"] == "promo"
    rep = validate("2026-10-18")
    assert rep["games"]["dbs"]["status"] == "ok", rep["games"]["dbs"]["findings"]


def test_best_buy_supply_threshold():
    from tcgwatcher.build import best_buy
    s = {"flags": [], "ev_eu_excl_saa": 160.0, "retail": [], "ebay_box": None,
         "box": {"eu": {"landed": 150.0, "landed_via": "zero", "url": "u", "n": 1, "landed_note": "x"}}}
    b = best_buy(s, min_offers=3)
    assert b["thin"] and b["offers"] == 1 and "thin" in s["flags"]            # one seller, one box: indication only
    s2 = {"flags": [], "ev_eu_excl_saa": 160.0, "ebay_box": {"low": 140.0, "url": "e", "n": 4, "median": 155.0},
          "retail": [{"price": 149.0, "status": "in_stock", "kind": "box", "retailer": "collgamers", "url": "c"}],
          "box": {"eu": {"landed": 150.0, "landed_via": "zero", "url": "u", "n": 2, "landed_note": "x"}}}
    b2 = best_buy(s2, min_offers=3)
    assert not b2["thin"] and b2["offers"] == 7 and b2["source"].startswith("eBay") and b2["price"] == 140.0
