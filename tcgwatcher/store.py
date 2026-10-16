"""On-disk layout (everything under data/ is committed; raw source data is never rewritten by later steps).

data/catalog/<category>/_groups.json          current group list for the category (as returned by the source)
data/catalog/<category>/<groupId>.json        current product catalog of the group (rewritten only when it changes)
data/prices/<category>/<YYYY-MM-DD>.json      all price rows of the category for that source day (immutable)
data/fx/<YYYY-MM-DD>.json                     USD→EUR rate used that day
data/quality/<YYYY-MM-DD>.json                validation report
data/state/*.json                             pipeline state (known groups, sent alerts, last sync)
data/alerts/<YYYY-MM-DD>.json                 alerts produced that day (also when Telegram is not configured)
"""
import hashlib
import json
import os
from pathlib import Path
from . import DATA


def _p(*parts) -> Path:
    p = DATA.joinpath(*[str(x) for x in parts])
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def read_json(path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path, obj, compact=True):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        if compact:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        else:
            json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, path)


def digest(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]


# --- catalog -------------------------------------------------------------------------------------
def groups_path(cat): return _p("catalog", cat, "_groups.json")
def catalog_path(cat, gid): return _p("catalog", cat, f"{gid}.json")
def prices_path(cat, day): return _p("prices", cat, f"{day}.json")
def fx_path(day): return _p("fx", f"{day}.json")
def quality_path(day): return _p("quality", f"{day}.json")
def alerts_path(day): return _p("alerts", f"{day}.json")
def state_path(name): return _p("state", f"{name}.json")


def list_price_days(cat):
    d = DATA / "prices" / str(cat)
    if not d.exists():
        return []
    return sorted(p.stem for p in d.glob("????-??-??.json"))


def list_catalog_groups(cat):
    d = DATA / "catalog" / str(cat)
    if not d.exists():
        return []
    return sorted(int(p.stem) for p in d.glob("*.json") if p.stem.isdigit())
