"""Normalize raw catalog + price rows into a game-agnostic in-memory model.

Canonical card key:  <GAME>:<SET>:<NUMBER>:<VARIANT>   e.g. dbfw:FB10:FB10-121:SAA
Sealed product key:  <GAME>:<SET>:sealed:<kind>        e.g. dbfw:FB10:sealed:box
TCGplayer productId is kept on every record as the source id; other sources map onto the canonical key later.
"""
import re
from dataclasses import dataclass, field
from .config import group_kind
from . import store

PRICE_PREF = ("marketPrice", "midPrice", "lowPrice")


@dataclass
class Product:
    game: str
    set_code: str
    group_id: int
    product_id: int
    name: str
    rarity: str
    number: str            # '' for sealed/non-card
    variant: str           # '' | AA | SAA | GOLD | DBP ...
    hit_class: str | None  # SR, SR_AA, SCR, ... or None (base card) / None for sealed
    sealed_kind: str | None  # box | case | pack | None
    presale: bool
    price: float | None = None
    price_src: str | None = None    # market | mid | low
    prices: dict = field(default_factory=dict)   # raw: subTypeName → {marketPrice, lowPrice, midPrice, highPrice}
    image: str | None = None
    url: str | None = None

    @property
    def key(self):
        if self.sealed_kind:
            return f"{self.game}:{self.set_code}:sealed:{self.sealed_kind}"
        return f"{self.game}:{self.set_code}:{self.number or 'p' + str(self.product_id)}:{self.variant}"


def _ext(product):
    return {e["name"]: e.get("value") for e in product.get("extendedData") or []}


def _match_rule(rule, name, rarity):
    if "rarity" in rule and rarity != rule["rarity"]:
        return False
    if "rarity_in" in rule and rarity not in rule["rarity_in"]:
        return False
    if "name_contains" in rule and rule["name_contains"] not in name:
        return False
    return True


def classify(game, name, rarity):
    for c in game.get("classes", []):
        if _match_rule(c["rule"], name, rarity):
            return c["id"]
    return None


def variant_of(game, name):
    for v in game.get("variants", []):
        if v["suffix"] in name:
            return v["code"]
    return ""


def sealed_kind_of(game, name, number):
    if number:
        return None
    n = name.strip()
    for kind in ("case", "box", "pack"):          # case before box: 'Booster Box Case' ends with 'Case'
        r = game["sealed"].get(kind)
        if not r:
            continue
        if n.endswith(r["endswith"]) and not any(x in n for x in r.get("not_contains", [])):
            return kind
    return None


def class_estimates(game):
    """id → est (first occurrence wins; duplicate ids share one estimate)."""
    est = {}
    labels = {}
    for c in game.get("classes", []):
        est.setdefault(c["id"], c["est"])
        labels.setdefault(c["id"], c["label"])
    return est, labels


def pick_price(price_rows_for_product):
    """One number per product: market, else mid, else low. Prefers the 'Normal'/'Holofoil' row with a market price."""
    best = None
    for sub, p in price_rows_for_product.items():
        for src in PRICE_PREF:
            v = p.get(src)
            if v is not None and v > 0:
                cand = (PRICE_PREF.index(src), v, src)
                if best is None or cand[0] < best[0]:
                    best = cand
                break
    if best is None:
        return None, None
    return best[1], {"marketPrice": "market", "midPrice": "mid", "lowPrice": "low"}[best[2]]


def load_game(game, day):
    """Build the model for one game from the current catalog and the price file of `day`."""
    cat = game["tcgcsv_category"]
    groups_doc = store.read_json(store.groups_path(cat), {"results": []})
    groups = {g["groupId"]: g for g in groups_doc["results"]}
    price_doc = store.read_json(store.prices_path(cat, day), {"results": []})
    prices = {}
    for r in price_doc["results"]:
        prices.setdefault(r["productId"], {})[r.get("subTypeName") or "Normal"] = {
            k: r.get(k) for k in ("marketPrice", "lowPrice", "midPrice", "highPrice")}
    products = []
    sets = {}
    fixes = {int(k): v for k, v in (game.get("number_fixes") or {}).items()}
    for gid in store.list_catalog_groups(cat):
        doc = store.read_json(store.catalog_path(cat, gid))
        grp = groups.get(gid) or doc.get("group") or {}
        abbr = grp.get("abbreviation") or f"G{gid}"
        kind = group_kind(game, abbr)
        sets[abbr] = {"code": abbr, "name": grp.get("name"), "group_id": gid, "kind": kind,
                      "published": (grp.get("publishedOn") or "")[:10], "modified": (grp.get("modifiedOn") or "")[:10]}
        for p in doc["results"]:
            ext = _ext(p)
            name = p["name"]
            rarity = ext.get("Rarity") or ""
            number = fixes.get(p["productId"]) or ext.get("Number") or ""
            sealed = sealed_kind_of(game, name, number)
            pr = prices.get(p["productId"], {})
            price, src = pick_price(pr)
            products.append(Product(
                game=game["id"], set_code=abbr, group_id=gid, product_id=p["productId"], name=name, rarity=rarity,
                number=number, variant=variant_of(game, name) if number else "",
                hit_class=classify(game, name, rarity) if (number and kind == "booster") else None, sealed_kind=sealed,
                presale=bool((p.get("presaleInfo") or {}).get("isPresale")), price=price, price_src=src, prices=pr,
                image=p.get("imageUrl"), url=p.get("url")))
    return {"game": game, "day": day, "sets": sets, "products": products,
            "source_last_updated": price_doc.get("source_last_updated"), "fetched_at": price_doc.get("fetched_at")}
