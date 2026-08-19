#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
webui/mock_api.py — мок-бэкенд для локального превью GUI.

Имитирует реальную схему inventory.f2c.ru:
организации → адреса → инвентаризации → оборудование,
включая auth (login/refresh/me), CRUD карточек, действия
found/unfound/transfer/review, фото, поиск по серийному номеру.

Запуск: python3 webui/mock_api.py [host] [port]   (по умолчанию 127.0.0.1:8799)
Демо-вход: любой email, пароль "secret".
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

ORGS = [
    {"id": 10, "name": "ГБОУ Школа № 1557"},
    {"id": 11, "name": "Колледж № 32"},
]
ADDRS = {
    10: [
        {"id": 101, "full_address": "г. Москва, ул. Ленина, д. 5, стр. 1",
         "city": "Москва", "street": "Ленина", "house": "5"},
        {"id": 102, "full_address": "г. Москва, ул. Свободы, д. 10",
         "city": "Москва", "street": "Свободы", "house": "10"},
        {"id": 104, "full_address": "г. Москва, ул. Ленина, д. 21",
         "city": "Москва", "street": "Ленина", "house": "21"},
    ],
    11: [
        {"id": 103, "full_address": "г. Зеленоград, пр-т Мира, д. 2",
         "city": "Зеленоград", "street": "Мира", "house": "2"},
    ],
}
INVENTORIES = [
    {"id": 7, "title": "Инвентаризация 2026", "status": "active"},
    {"id": 8, "title": "Инвентаризация 2025", "status": "completed"},
]
EQUIP = [
    {"id": 500, "name": "Ноутбук HP 250", "serial_number": "SN500",
     "inventory_number": "INV-1001", "address_id": 101, "status": "active",
     "comment": "каб. 12"},
    {"id": 501, "name": "Проектор Epson EB-X05", "serial_number": "SN501",
     "inventory_number": "INV-1002", "address_id": 101, "status": "active",
     "comment": ""},
    {"id": 502, "name": "МФУ Canon i-SENSYS", "serial_number": "SN502",
     "inventory_number": "INV-1003", "address_id": 102, "status": "repair",
     "comment": "в сервисе"},
    {"id": 503, "name": "Сервер Dell R740", "serial_number": "SN503",
     "inventory_number": "INV-1004", "address_id": 104, "status": "active",
     "comment": "серверная"},
    {"id": 504, "name": "Интерактивная панель", "serial_number": "SN504",
     "inventory_number": "INV-1005", "address_id": 103, "status": "active",
     "comment": ""},
]
HISTORY = {
    500: [{"ts": "2026-08-01 10:00", "event": "created", "by": "system"},
          {"ts": "2026-08-10 14:22", "event": "review", "by": "i.ivanov",
           "note": "в норме"},
          {"ts": "2026-08-12 09:05", "event": "transfer", "by": "i.ivanov",
           "note": "адрес 102 → 101"}],
    502: [{"ts": "2026-08-11 16:40", "event": "repair", "by": "p.petrov"}],
}
PHOTOS = {
    500: [{"id": 1, "url": "https://cdn.example/photos/500_front.jpg",
           "label": "вид спереди"},
          {"id": 2, "url": "https://cdn.example/photos/500_serial.jpg",
           "label": "серийный номер"}],
}
SERIAL_DB = {r["serial_number"]: r for r in EQUIP}
NEXT_EQUIP = 600
NEXT_PHOTO = 100

VALID_TOKENS = {"tok123", "tok789"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj=None, raw=None, ctype="application/json"):
        body = raw if raw is not None else json.dumps(obj, ensure_ascii=False)
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body.encode())))
        self.end_headers()
        self.wfile.write(body.encode())

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) or b"{}"
        try:
            return json.loads(raw)
        except ValueError:
            return {}

    def _auth(self):
        return self.headers.get("Authorization") in (
            "Bearer " + t for t in VALID_TOKENS)

    def _guarded(self):
        if not self._auth():
            self._send(401, {"success": False, "error": {
                "code": "UNAUTHORIZED", "message": "No token"}})
            return False
        return True

    def do_GET(self):
        u = urlparse(self.path)
        path = u.path
        q = parse_qs(u.query)

        if path == "/api/auth/me":
            if not self._guarded():
                return
            return self._send(200, {"success": True, "data": {
                "id": 7, "email": "user@f2c.ru", "name": "Иванов Иван",
                "role": "manager"}})
        if path == "/api/organizations":
            if not self._guarded():
                return
            return self._send(200, {"success": True, "data": ORGS})
        if path == "/api/inventories":
            if not self._guarded():
                return
            return self._send(200, {"success": True, "data": INVENTORIES})
        if path == "/api/addresses":
            if not self._guarded():
                return
            all_a = [dict(a, org_id=k) for k, v in ADDRS.items() for a in v]
            return self._send(200, {"success": True, "data": all_a})
        if path.startswith("/api/addresses/"):
            if not self._guarded():
                return
            rid = int(path.rsplit("/", 1)[1])
            for v in ADDRS.values():
                for a in v:
                    if a["id"] == rid:
                        return self._send(200, {"success": True, "data": a})
        if path == "/api/equipment":
            if not self._guarded():
                return
            rows = list(EQUIP)
            if q.get("address_id"):
                rows = [r for r in rows
                        if str(r["address_id"]) == q["address_id"][0]]
            if q.get("inventory_id"):
                rows = [r for r in rows if q["inventory_id"][0] == "7"]
            return self._send(200, {"success": True, "data": rows})
        if path == "/api/equipment/serial-lookup/manual":
            if not self._guarded():
                return
            return self._send(200, {"success": True, "data": EQUIP[0]})
        if path == "/api/journals/meta":
            if not self._guarded():
                return
            return self._send(200, {"success": True, "data": {
                "items": [{"name": "Журнал оборудования", "id": 1}]}})
        parts = path.split("/")
        if path.startswith("/api/organizations/"):
            # /api/organizations/{org}/addresses
            if len(parts) == 5 and parts[4] == "addresses" and parts[3].isdigit():
                if not self._guarded():
                    return
                return self._send(200, {"success": True,
                                        "data": ADDRS.get(int(parts[3]), [])})
        if path.startswith("/api/equipment/"):
            if len(parts) == 4 and parts[3].isdigit():
                if not self._guarded():
                    return
                rid = int(parts[3])
                for r in EQUIP:
                    if r["id"] == rid:
                        return self._send(200, {"success": True, "data": r})
            if len(parts) == 5 and parts[3].isdigit():
                if not self._guarded():
                    return
                rid = int(parts[3])
                if parts[4] == "history":
                    return self._send(200, {"success": True,
                                            "data": HISTORY.get(rid, [])})
                if parts[4] == "photos":
                    return self._send(200, {"success": True,
                                            "data": PHOTOS.get(rid, [])})
            if len(parts) == 7 and parts[3] == "inventories":
                if not self._guarded():
                    return
                return self._send(200, {"success": True, "data": {
                    "id": int(parts[5]), "action": parts[6], "ok": True}})
        return self._send(404, {"success": False, "error": {
            "code": "NOT_FOUND", "message": "Resource not found"}})

    def _equip(self, rid):
        for r in EQUIP:
            if r["id"] == rid:
                return r
        return None

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/auth/login":
            body = self._body()
            if body.get("password") == "secret":
                return self._send(200, {"success": True, "data": {
                    "access_token": "tok123", "refresh_token": "rt456",
                    "user": {"id": 7, "email": body.get("email")}}})
            return self._send(401, {"success": False, "error": {
                "code": "INVALID_CREDENTIALS",
                "message": "Неверная почта или пароль"}})
        if path == "/api/auth/refresh":
            body = self._body()
            if body.get("refresh_token") == "rt456":
                return self._send(200, {"success": True, "data": {
                    "access_token": "tok789", "refresh_token": "rt456"}})
            return self._send(401, {"success": False, "error": {
                "code": "INVALID_TOKEN", "message": "Bad refresh"}})
        if path == "/api/equipment":
            if not self._guarded():
                return
            global NEXT_EQUIP
            body = self._body()
            if not body.get("name"):
                return self._send(422, {"success": False, "error": {
                    "code": "VALIDATION", "message": "name обязателен"}})
            rec = dict(body)
            rec.setdefault("status", "active")
            rec["id"] = NEXT_EQUIP
            NEXT_EQUIP += 1
            EQUIP.append(rec)
            return self._send(201, {"success": True, "data": rec})
        if path == "/api/equipment/serial-lookup/photo":
            if not self._guarded():
                return
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            return self._send(200, {"success": True, "data": {
                "serial_number": "SN500", "confidence": 0.97}})
        parts = path.split("/")
        if len(parts) == 5 and parts[3].isdigit():
            if not self._guarded():
                return
            rid = int(parts[3])
            action = parts[4]
            rec = self._equip(rid)
            if not rec:
                return self._send(404, {"success": False, "error": {
                    "code": "NOT_FOUND", "message": "Resource not found"}})
            body = self._body()
            if action == "transfer":
                rec["address_id"] = int(body.get("address_id") or rec["address_id"])
                rec["status"] = "active"
            elif action in ("found", "unfound"):
                rec["status"] = action
            elif action == "review":
                rec["review_comment"] = body.get("comment", "")
            elif action == "photos":
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                global NEXT_PHOTO
                PHOTOS.setdefault(rid, []).append(
                    {"id": NEXT_PHOTO, "url": "https://cdn.example/photos/new.jpg",
                     "label": "новое фото"})
                NEXT_PHOTO += 1
                return self._send(201, {"success": True,
                                        "data": {"uploaded": True}})
            else:
                return self._send(404, {"success": False, "error": {
                    "code": "NOT_FOUND", "message": "Resource not found"}})
            return self._send(200, {"success": True, "data": rec})
        if len(parts) == 7 and parts[3] == "inventories":
            if not self._guarded():
                return
            return self._send(200, {"success": True, "data": {
                "id": int(parts[5]), "action": parts[6], "ok": True}})
        return self._send(404, {"success": False, "error": {
            "code": "NOT_FOUND", "message": "Resource not found"}})

    def do_PUT(self):
        path = urlparse(self.path).path
        parts = path.split("/")
        if len(parts) == 4 and parts[3].isdigit():
            if not self._guarded():
                return
            rec = self._equip(int(parts[3]))
            if not rec:
                return self._send(404, {"success": False, "error": {
                    "code": "NOT_FOUND", "message": "Resource not found"}})
            body = self._body()
            rec.update({k: v for k, v in body.items() if k != "id"})
            return self._send(200, {"success": True, "data": rec})
        return self._send(404, {"success": False, "error": {
            "code": "NOT_FOUND", "message": "Resource not found"}})

    do_PATCH = do_PUT

    def do_DELETE(self):
        path = urlparse(self.path).path
        parts = path.split("/")
        if len(parts) == 4 and parts[3].isdigit():
            if not self._guarded():
                return
            rid = int(parts[3])
            EQUIP[:] = [r for r in EQUIP if r["id"] != rid]
            return self._send(200, {"success": True, "data": {"deleted": rid}})
        return self._send(404, {"success": False, "error": {
            "code": "NOT_FOUND", "message": "Resource not found"}})


if __name__ == "__main__":
    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8799
    print(f"mock api: http://{host}:{port}")
    HTTPServer((host, port), Handler).serve_forever()
