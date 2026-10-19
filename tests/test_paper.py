from tcgwatcher import paper, store


def latest():
    sets = [{"code": "FB09", "name": "Dual Evolution", "flags": [], "ev_eu_excl_saa": 173.0, "best_buy": {"price": 187.0, "source": "CardTrader Zero", "ratio": 0.93}},
            {"code": "FB10", "name": "Cross Force", "flags": [], "ev_eu_excl_saa": 104.0, "best_buy": {"price": 168.0, "source": "CardTrader Zero", "ratio": 0.62}},
            {"code": "FB12", "name": "Reach the God", "flags": ["presale", "no_cards"], "ev_eu_excl_saa": 0, "best_buy": {"price": 130.0, "source": "x", "ratio": 0}}]
    cards = [{"key": "dbfw:FB10:FB10-097:AA", "price": 70.0}]
    return {"fx": {"usd_eur": 0.9}, "games": {"dbfw": {"sets": sets, "cards": cards,
            "grade_signals": [{"key": "dbfw:FB10:FB10-097:AA", "set": "FB10", "name": "Cell", "number": "FB10-097", "class": "SR_AA", "raw_eur": 63.0,
                               "top_median": 400.0, "net": 112.0}],
            "slab_signals": []}}}


def test_positions_open_once_and_control_cohort(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA", tmp_path)
    monkeypatch.setattr(paper, "PATH", tmp_path / "paper" / "positions.json")
    L = latest()
    added = paper.open_positions("2026-10-19", L, [], active_buy={"FB09"})      # KOOP episode already active → held, not control
    kinds = sorted((p["kind"], p["code"]) for p in added)
    assert kinds == [("box", "FB09"), ("control", "FB10"), ("raw", "FB10")]      # FB12 presale excluded, FB09 not in control
    assert paper.open_positions("2026-10-20", L, [{"kind": "buy", "key": "buy:dbfw:FB09"}]) == []   # idempotent
    # a month later: new control cohort, FB09 still open → not in control
    added = paper.open_positions("2026-11-01", L, [])
    assert [p["kind"] for p in added] == ["control"] and added[0]["code"] == "FB10"


def test_report_values_with_same_yardstick(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA", tmp_path)
    monkeypatch.setattr(paper, "PATH", tmp_path / "paper" / "positions.json")
    monkeypatch.setattr(paper, "ROOT", tmp_path)
    (tmp_path / "docs").mkdir()
    L = latest()
    paper.open_positions("2026-10-19", L, [{"kind": "buy", "key": "buy:dbfw:FB09"}])
    L2 = latest()
    L2["games"]["dbfw"]["sets"][0]["best_buy"]["price"] = 210.0      # FB09 box up
    L2["games"]["dbfw"]["sets"][1]["best_buy"]["price"] = 150.0      # control down
    L2["games"]["dbfw"]["cards"][0]["price"] = 90.0                  # raw card up (€81)
    import tcgwatcher.ebay as ebay
    monkeypatch.setattr(ebay, "latest_day", lambda: None)
    md = paper.report("2026-11-01", L2, send=False)
    assert "| 🎯 boxen (koop) | 1 | €187 | €210 | +23 (+12.3%) | 1/1 |" in md
    assert "| ⚪ controlegroep | 1 | €168 | €150 | -18 (-10.7%) | 0/1 |" in md
    assert "📈 raw kaarten | 1 | €63 | €81 | +18" in md and "de ratio voegt iets toe" in md
    assert (tmp_path / "docs" / "paper-2026-11.md").exists()
