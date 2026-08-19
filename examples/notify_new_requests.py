#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Пример пользовательского процесса для f2c_inventory.py.

Следит за появлением НОВЫХ заявок (ресурс "requests") и отправляет
уведомление в Telegram. Запуск по расписанию (cron/Планировщик задач):

    python3 f2c_inventory.py run examples/notify_new_requests.py

Переменные окружения:
    TG_BOT_TOKEN — токен бота Telegram
    TG_CHAT_ID   — ID чата/канала
    WATCH_RESOURCE — имя ресурса (по умолчанию "requests")

Контекст, доступный внутри процесса (см. f2c_inventory.run_process):
    api      — HTTP-клиент с сохранённой сессией
    auth     — объект Auth (login, me, ...)
    api_map  — карта эндпоинтов (результат recon)
    ApiError, print_table, now_iso

Внимание: процесс работает только если ранее выполнен `f2c_inventory.py login`.
"""

import json
import os
import urllib.request
from pathlib import Path

# api, api_map, now_iso, auth, ApiError, print_table — внедряются в контекст
# процесса функцией f2c_inventory.run_process.

RESOURCE = os.environ.get("WATCH_RESOURCE", "requests")
_CFG = Path(os.environ.get("F2C_CONFIG_DIR", "~/.config/f2c-inventory")).expanduser()
STATE_FILE = _CFG / "notify_state.json"

# --- Telegram ---------------------------------------------------------------


def tg_send(text: str) -> bool:
    token = os.environ.get("TG_BOT_TOKEN", "")
    chat_id = os.environ.get("TG_CHAT_ID", "")
    if not token or not chat_id:
        print("[!] Не заданы TG_BOT_TOKEN / TG_CHAT_ID — печатаю в консоль")
        print(text)
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({"chat_id": chat_id, "text": text}).encode()
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.status == 200


# --- снимок заявок -----------------------------------------------------------

def fetch_requests() -> dict:
    path = None
    for ep in (api_map or {}).get("endpoints", []):
        p = ep.get("path", "")
        if RESOURCE.lower() in p.lower().split("/")[-1].replace("{", "").replace("}", ""):
            path = p
            break
    if not path:
        path = "/api/v1/" + RESOURCE
    rows = []
    page, limit = 1, 100
    while True:
        resp = api.get(path, params={"page": page, "limit": limit})
        batch = resp.as_list()
        rows.extend(batch)
        if len(batch) < limit:
            break
        page += 1
    return {str(r.get("id", i)): r for i, r in enumerate(rows) if isinstance(r, dict)}


# --- main --------------------------------------------------------------------

def main() -> None:
    prev: dict = {}
    if STATE_FILE.exists():
        try:
            prev = json.loads(STATE_FILE.read_text("utf-8"))
        except (OSError, ValueError):
            prev = {}

    cur = fetch_requests()
    new_ids = sorted(set(cur) - set(prev))
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(cur, ensure_ascii=False, default=str), "utf-8")

    if not prev:
        print(f"[i] Первый запуск: зафиксировано {len(cur)} заявок, "
              "уведомления пойдут со следующего раза")
        return

    if not new_ids:
        print(f"[i] {now_iso()}: новых заявок нет (всего {len(cur)})")
        return

    print(f"[i] Новых заявок: {len(new_ids)}")
    for rid in new_ids:
        r = cur[rid]
        title = r.get("title") or r.get("name") or r.get("subject") or "(без названия)"
        status = r.get("status") or r.get("state") or "?"
        text = f"🆕 Новая заявка #{rid}\n{title}\nСтатус: {status}"
        tg_send(text)


main()
