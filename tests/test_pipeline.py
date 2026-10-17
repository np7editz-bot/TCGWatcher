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


def test_validation_finds_known_source_errors():
    rep = validate(DAY)
    codes = {f["code"] for f in rep["games"]["dbfw"]["findings"]}
    assert "duplicate_keys" in codes            # FB10-068 appears twice in TCGplayer's catalog
    assert "number_name_mismatch" in codes      # 'Piccolo - FB10-067' carries Number FB10-068
    assert rep["status"] in ("ok", "warn")      # never fail on the seed


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
    assert dup and all(not k.split(":")[1].startswith("DBSFW") for k in dup[0]["ids"])
