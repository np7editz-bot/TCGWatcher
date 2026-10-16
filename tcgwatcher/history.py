"""Price history across days, read straight from the immutable daily price files.

series(cat) → {productId: [(day, price, src), ...]} using the same market→mid→low preference as the model,
so a trend never silently mixes 'market' with 'listing' prices without saying so (src is kept per point).
"""
import datetime as dt
from .model import pick_price
from . import store


def load_series(cat, days=None):
    days = days or store.list_price_days(cat)
    series = {}
    for day in days:
        doc = store.read_json(store.prices_path(cat, day), {"results": []})
        rows = {}
        for r in doc["results"]:
            rows.setdefault(r["productId"], {})[r.get("subTypeName") or "Normal"] = {
                "marketPrice": r.get("marketPrice"), "midPrice": r.get("midPrice"), "lowPrice": r.get("lowPrice")}
        for pid, subs in rows.items():
            price, src = pick_price(subs)
            if price is not None:
                series.setdefault(pid, []).append((day, price, src))
    return series


def change_pct(points, day, window_days, market_only=True):
    """% change of the value on `day` vs the mean over the `window_days` before it. None when not enough data."""
    if not points:
        return None
    cur = next((p for p in points if p[0] == day), None)
    if cur is None:
        return None
    d0 = dt.date.fromisoformat(day)
    hist = [p for p in points if p[0] < day and (d0 - dt.date.fromisoformat(p[0])).days <= window_days
            and (p[2] == "market" or not market_only)]
    if len(hist) < max(2, window_days // 3):
        return None
    base = sum(p[1] for p in hist) / len(hist)
    if base <= 0:
        return None
    return round((cur[1] - base) / base * 100, 1)


def sparkline(points, day, window_days=30):
    d0 = dt.date.fromisoformat(day)
    return [[p[0], p[1]] for p in points if (d0 - dt.date.fromisoformat(p[0])).days <= window_days]
