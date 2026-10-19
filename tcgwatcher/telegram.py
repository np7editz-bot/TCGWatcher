"""Telegram delivery. Needs env TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID (repo secrets).
Without a token the pipeline still runs: alerts are written to data/alerts/<day>.json only.

send(text, photo=None, buttons=None): one message. With `photo` (https URL) the image shows above the text
(link preview, large), so long texts (up to 4096 chars) keep their picture. `buttons` = [[(label, url), ...], ...].
commands(): reads /mute SET, /unmute SET, /status sent to the bot since the last run (getUpdates with offset)."""
import logging
import os
import re
import requests
from . import store

log = logging.getLogger("tcgwatcher.telegram")
API = "https://api.telegram.org/bot{token}/{method}"


def configured():
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN"))


def _token():
    return os.environ.get("TELEGRAM_BOT_TOKEN")


def send(text, photo=None, buttons=None, chat_id=None):
    token = _token()
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
    payload = {"chat_id": chat_id, "text": text[:4096], "parse_mode": "HTML"}
    if photo:
        payload["link_preview_options"] = {"url": photo, "prefer_large_media": True, "show_above_text": True}
    else:
        payload["link_preview_options"] = {"is_disabled": True}
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": [[{"text": l, "url": u} for l, u in row] for row in buttons]}
    r = requests.post(API.format(token=token, method="sendMessage"), timeout=30, json=payload)
    if r.status_code != 200:
        log.error("telegram sendMessage failed: %s %s", r.status_code, r.text[:200])
        return False
    return True


CMD = re.compile(r"^/(mute|unmute|status)(?:@\w+)?\s*([A-Za-z]{2}\d{2}|all)?", re.I)


def commands():
    """Collect bot commands sent since the last run. Returns list of (cmd, arg). Offset kept in data/state/telegram.json."""
    token = _token()
    if not token:
        return []
    st = store.read_json(store.state_path("telegram"), {"offset": 0})
    try:
        r = requests.get(API.format(token=token, method="getUpdates"), params={"offset": st["offset"], "timeout": 0}, timeout=30).json()
    except Exception as e:  # noqa: BLE001
        log.warning("getUpdates failed: %s", e)
        return []
    out = []
    for u in r.get("result", []):
        st["offset"] = max(st["offset"], u["update_id"] + 1)
        text = (u.get("message") or {}).get("text") or ""
        m = CMD.match(text.strip())
        if m:
            out.append((m.group(1).lower(), (m.group(2) or "").upper()))
    store.write_json(store.state_path("telegram"), st, compact=False)
    return out
