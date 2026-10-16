"""TCGWatcher — daily price intelligence for sealed TCG product (English market).

Pipeline: fetch (raw, immutable) → validate (quality gate) → build (scores, trends, site data) → alerts.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CONFIG = ROOT / "config"
SITE = ROOT / "site"
USER_AGENT = "TCGWatcher/1.0 (+https://github.com/np7editz-bot/TCGWatcher; daily sync, 1x/day)"
