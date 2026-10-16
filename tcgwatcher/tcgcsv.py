"""Minimal tcgcsv.com client. Etiquette per https://tcgcsv.com/docs: custom User-Agent, 100 ms between
requests, one sync per day, check last-updated.txt first."""
import time
import requests
from . import USER_AGENT

BASE = "https://tcgcsv.com"


class TcgCsv:
    def __init__(self, session=None, sleep=0.12, retries=3):
        self.s = session or requests.Session()
        self.s.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
        self.sleep = sleep
        self.retries = retries
        self.requests_made = 0

    def _get(self, path, json=True):
        url = f"{BASE}{path}"
        last = None
        for attempt in range(self.retries):
            try:
                r = self.s.get(url, timeout=60)
                self.requests_made += 1
                time.sleep(self.sleep)
                if r.status_code == 429:
                    time.sleep(30 * (attempt + 1))
                    continue
                r.raise_for_status()
                return r.json() if json else r.text
            except requests.RequestException as e:   # noqa: PERF203
                last = e
                time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"tcgcsv GET {path} failed after {self.retries} attempts: {last}")

    def last_updated(self):
        return self._get("/last-updated.txt", json=False).strip()

    def categories(self):
        return self._get("/tcgplayer/categories")["results"]

    def groups(self, category_id):
        return self._get(f"/tcgplayer/{category_id}/groups")["results"]

    def products(self, category_id, group_id):
        return self._get(f"/tcgplayer/{category_id}/{group_id}/products")["results"]

    def prices(self, category_id, group_id):
        return self._get(f"/tcgplayer/{category_id}/{group_id}/prices")["results"]
