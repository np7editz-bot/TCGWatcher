import re
import yaml
from . import CONFIG


def load_games():
    with open(CONFIG / "games.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    games = {}
    for gid, g in cfg["games"].items():
        if not g.get("enabled", True):
            continue
        g = dict(g)
        g["id"] = gid
        g["_kinds"] = {k: re.compile(v) for k, v in g.get("group_kinds", {}).items()}
        games[gid] = g
    return games


def load_alerts():
    with open(CONFIG / "alerts.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def group_kind(game, abbreviation):
    """booster | starter | promo | other, from the group abbreviation."""
    abbr = abbreviation or ""
    for kind in ("promo", "booster", "starter"):   # promo first: 'FB11_PR' must not match booster
        rx = game["_kinds"].get(kind)
        if rx and rx.search(abbr):
            return kind
    return "other"
