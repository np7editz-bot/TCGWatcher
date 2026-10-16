"""Telegram delivery. Needs env TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID (repo secrets).
Without a token the pipeline still runs: alerts are written to data/alerts/<day>.json only."""
import logging
import os
import requests

log = logging.getLogger("tcgwatcher.telegram")
API = "https://api.telegram.org/bot{token}/{method}"


def configured():
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN"))


def send(text, chat_id=None):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
    if not token:
        log.info("no TELEGRAM_BOT_TOKEN; not sending")
        return False
    if not chat_id:
        log.warning("TELEGRAM_CHAT_ID missing. Send /start to your bot, then look for 'chat id' below:")
        try:
            r = requests.get(API.format(token=token, method="getUpdates"), timeout=30).json()
            ids = {(u.get("message") or {}).get("chat", {}).get("id") for u in r.get("result", [])}
            log.warning("candidate chat ids: %s", sorted(i for i in ids if i))
        except Exception as e:  # noqa: BLE001
            log.warning("getUpdates failed: %s", e)
        return False
    r = requests.post(API.format(token=token, method="sendMessage"), timeout=30,
                      json={"chat_id": chat_id, "text": text[:4000], "parse_mode": "HTML", "disable_web_page_preview": True})
    if r.status_code != 200:
        log.error("telegram sendMessage failed: %s %s", r.status_code, r.text[:200])
        return False
    return True
