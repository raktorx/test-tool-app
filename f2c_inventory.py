#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
f2c_inventory.py — CLI-инструмент для работы с https://inventory.f2c.ru/

Личный кабинет команды учёта оборудования (F2C): заявки, пользователи,
организации, склады, инвентарные карточки, остатки, движение техники.

Инструмент умеет:
  * recon    — «вскрыть» API: скачать JS-бандлы фронтенда и составить карту
               эндпоинтов (метод + путь). Полезно, т.к. публичной документации
               у системы нет, а карта получается автоматически из кода сайта;
  * login    — войти по рабочей почте и паролю, сохранить токен сессии;
  * status   — проверить сохранённую сессию;
  * call     — произвольный запрос к API (GET/POST/PUT/PATCH/DELETE);
  * list/get/create/update/delete — типовые операции с ресурсами;
  * export   — выгрузить весь ресурс (со всеми страницами) в CSV/JSON;
  * import   — массово создать/обновить записи из CSV (автоматизация);
  * watch    — следить за изменениями ресурса (новые заявки и т.п.);
  * run      — выполнить свой python-скрипт-«процесс» с готовым контекстом
               (см. examples/).

Зависимости: Python 3.8+, requests (curl_cffi — опционально, для обхода
проверок TLS-отпечатка браузера).

Быстрый старт:
    python3 f2c_inventory.py recon
    python3 f2c_inventory.py login
    python3 f2c_inventory.py list requests --all --csv requests.csv
"""

import argparse
import csv as csv_mod
import datetime as _dt
import getpass
import hashlib
import json
import os
import re
import sys
import textwrap
import time
from pathlib import Path

VERSION = "0.1.0"
DEFAULT_BASE_URL = "https://inventory.f2c.ru"

try:
    import requests as std_requests  # type: ignore
except ImportError as _e:  # pragma: no cover
    std_requests = None
    _REQUESTS_IMPORT_ERROR = _e
else:
    _REQUESTS_IMPORT_ERROR = None

try:
    from curl_cffi import requests as cffi_requests  # type: ignore
except Exception:  # ImportError или RuntimeError (нет libcurl-impersonate)
    cffi_requests = None

# --------------------------------------------------------------------------
# Утилиты
# --------------------------------------------------------------------------

TOKEN_KEY_PRIORITY = [
    re.compile(r"access[_-]?token", re.I),   # 0
    re.compile(r"^token$|auth[_-]?token", re.I),  # 1
    re.compile(r"refresh[_-]?token", re.I),  # 2
    re.compile(r"jwt", re.I),                # 3
    re.compile(r"session", re.I),            # 4
    re.compile(r"api[_-]?key", re.I),        # 5
]

RESOURCE_WORDS = (
    "auth", "login", "logout", "signin", "signup", "token", "session",
    "user", "users", "employee", "profile", "me",
    "org", "organization", "organisation", "company", "branch", "department",
    "warehouse", "store", "stock", "stocks",
    "inventory", "inventories", "inv", "card", "cards",
    "equipment", "device", "devices", "item", "items", "asset", "assets", "thing",
    "request", "requests", "application", "applications", "order", "orders", "ticket",
    "movement", "movements", "transfer", "transfers", "move", "shipment",
    "history", "histories", "log", "logs", "audit",
    "report", "reports", "stat", "stats", "statistic", "dashboard", "summary",
    "file", "files", "upload", "uploads", "import", "exports", "export", "download",
    "search", "status", "statuses", "type", "types", "category", "categories",
    "role", "roles", "permission", "permissions", "setting", "settings",
    "balance", "balances", "remainder", "remainders", "residue", "count",
    "vendor", "vendors", "supplier", "suppliers", "manufacturer",
    "serial", "barcode", "qr", "location", "locations", "room", "rooms",
    "responsible", "owner", "holder", "comment", "comments", "note", "notes",
    "photo", "photos", "image", "images", "attachment", "attachments",
    "notification", "notifications", "message", "messages",
)

STATIC_EXT_RE = re.compile(
    r"\.(js|mjs|css|map|png|jpe?g|gif|svg|webp|ico|woff2?|ttf|otf|eot|"
    r"mp3|mp4|webm|pdf|zip|gz|json|xml|txt|html?)$", re.I)

PATH_LITERAL_RE = re.compile(r"""["'`](/(?![/\s])[^"'`\s<>]{1,250})["'`]""")

METHOD_BEFORE_RE = re.compile(
    r"\.(get|post|put|patch|delete)\s*\(\s*[^,()]{0,60}$|"
    r"method\s*[:=]\s*[\"'](GET|POST|PUT|PATCH|DELETE)[\"']\s*,?\s*[^,()]{0,60}$|"
    r"fetch\s*\(\s*[^,()]{0,60}$|"
    r"\.(request|send)\s*\(\s*[^,()]{0,60}$", re.I)

METHOD_AFTER_RE = re.compile(
    r"method\s*[:=]\s*[\"'](GET|POST|PUT|PATCH|DELETE)[\"']", re.I)

BASEURL_RE = re.compile(
    r"(?:baseURL|baseUrl|API_URL|apiUrl|api_url|VITE_[A-Z0-9_]+)"
    r"\s*[:=]\s*[\"']([^\"']+)[\"']")

LOGIN_PATH_HINT_RE = re.compile(r"(login|signin|sign-in|auth)", re.I)
ME_PATH_HINT_RE = re.compile(r"/(me|profile|current)(/|$|\?)", re.I)


def now_iso() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def config_dir() -> Path:
    base = os.environ.get("F2C_CONFIG_DIR")
    if base:
        return Path(base)
    if os.name == "nt":
        root = Path(os.environ.get("APPDATA") or Path.home())
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    return root / "f2c-inventory"


def ensure_private(path: Path) -> None:
    """Не оставляем токены доступными на чтение другим пользователям."""
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def flatten_dict(d: dict, prefix: str = "") -> dict:
    out: dict = {}
    for k, v in d.items():
        key = f"{prefix}.{k}" if prefix else str(k)
        if isinstance(v, dict):
            out.update(flatten_dict(v, key))
        elif isinstance(v, (list, tuple)):
            out[key] = json.dumps(v, ensure_ascii=False)
        else:
            out[key] = v
    return out


def smart_value(s: str):
    s = s.strip()
    if s == "":
        return None
    low = s.lower()
    if low in ("null", "none", "nil"):
        return None
    if low == "true":
        return True
    if low == "false":
        return False
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


def print_table(rows: list, max_cols: int = 10, max_width: int = 42) -> None:
    if not rows:
        print("(записей нет)")
        return
    keys: list = []
    for r in rows[:100]:
        for k in r.keys():
            if k not in keys:
                keys.append(k)
    if "id" in keys:
        keys = ["id"] + [k for k in keys if k != "id"]
    keys = keys[:max_cols]
    header = [str(k)[:max_width] for k in keys]
    lines = []
    for r in rows:
        line = []
        for k in keys:
            v = r.get(k, "")
            if isinstance(v, (dict, list)):
                v = json.dumps(v, ensure_ascii=False)
            v = "" if v is None else str(v)
            line.append(v.replace("\n", " ")[:max_width])
        lines.append(line)
    widths = [max(len(header[i]), *(len(ln[i]) for ln in lines)) for i in range(len(header))]
    sep = "  ".join("-" * w for w in widths)
    print("  ".join(h.ljust(w) for h, w in zip(header, widths)))
    print(sep)
    for ln in lines:
        print("  ".join(c.ljust(w) for c, w in zip(ln, widths)))
    if len(rows) > len(lines):
        print(f"... показаны первые {len(lines)} из {len(rows)}")


class ApiError(Exception):
    def __init__(self, code: str, message: str, status: int, url: str):
        super().__init__(f"[{status}] {code}: {message} ({url})")
        self.code = code
        self.message = message
        self.status = status
        self.url = url


class ApiResponse:
    """Ответ API с разворачиванием стандартной обёртки {success, error, data}."""

    def __init__(self, status: int, url: str, headers, body: str):
        self.status = status
        self.url = url
        self.headers = headers
        self.body = body
        try:
            self.json = json.loads(body) if body else None
        except (ValueError, TypeError):
            self.json = None

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def unwrap(self):
        """Возвращает payload без обёртки; бросает ApiError при ошибке."""
        j = self.json
        if isinstance(j, dict):
            if "success" in j and j["success"] is False:
                err = j.get("error") or {}
                raise ApiError(
                    str(err.get("code") or "ERROR"),
                    str(err.get("message") or "Неизвестная ошибка"),
                    self.status, self.url)
            if "error" in j and isinstance(j["error"], dict) and j["error"].get("code"):
                if not j.get("success", True):
                    err = j["error"]
                    raise ApiError(str(err.get("code") or "ERROR"),
                                   str(err.get("message") or "Ошибка"),
                                   self.status, self.url)
            if "success" in j and j["success"] is True and "data" in j:
                return j.get("data")
            if "data" in j:
                return j.get("data")
        if not self.ok:
            raise ApiError(f"HTTP_{self.status}", self.body[:300] or "HTTP error",
                           self.status, self.url)
        return j

    def as_list(self) -> list:
        data = self.unwrap()
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("items", "results", "records", "rows", "list", "content",
                        "data", "entities", "elements"):
                if isinstance(data.get(key), list):
                    return data[key]
            if "data" in data and isinstance(data["data"], dict):
                for key in ("items", "results", "records", "rows", "list"):
                    if isinstance(data["data"].get(key), list):
                        return data["data"][key]
        return []

    def pagination_hint(self) -> dict:
        """Пытается понять, как устроена пагинация в ответе."""
        data = self.unwrap()
        if not isinstance(data, dict):
            return {}
        for key in ("total", "total_count", "totalCount", "count", "last_page",
                    "lastPage", "pages", "has_more", "hasMore", "next_page",
                    "nextPage", "page", "current_page", "offset"):
            if data.get(key) is not None:
                return {"has_pagination": True, "total": data.get("total")
                        or data.get("total_count") or data.get("totalCount")
                        or data.get("count")}
        return {"has_pagination": False}


def find_token(obj, path: str = ""):
    """Рекурсивно ищет значение токена в JSON-ответе.

    Возвращает список кортежей (priority, key_path, value).
    """
    found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            key_path = f"{path}.{k}" if path else str(k)
            if isinstance(v, str) and len(v) >= 6 and v.strip():
                for i, rx in enumerate(TOKEN_KEY_PRIORITY):
                    if rx.search(str(k)):
                        found.append((i, key_path, v))
                        break
            if isinstance(v, (dict, list)):
                found.extend(find_token(v, key_path))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            found.extend(find_token(v, f"{path}[{i}]" if path else f"[{i}]"))
    return found


# --------------------------------------------------------------------------
# HTTP-клиент
# --------------------------------------------------------------------------

class Client:
    """Тонкая обёртка над requests / curl_cffi с ретраями и паузами."""

    def __init__(self, base_url: str = DEFAULT_BASE_URL, timeout: float = 30,
                 retries: int = 3, min_interval: float = 0.0,
                 verbose: bool = False, insecure: bool = False,
                 proxy: str = "", backend: str = "auto", token: str = "",
                 extra_headers: dict | None = None):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retries = retries
        self.min_interval = min_interval
        self.verbose = verbose
        self.insecure = insecure
        self.proxy = proxy
        self.backend = backend
        self.token = token
        self.extra_headers = dict(extra_headers or {})
        self._last_request_ts = 0.0
        self._session = None

    # -- внутренние ---------------------------------------------------------

    def _use_cffi(self, url: str) -> bool:
        if not cffi_requests:
            return False
        if self.backend == "requests":
            return False
        if self.backend == "cffi":
            return True
        return url.startswith("https://")

    def _make_session(self):
        if self._use_cffi(self.base_url):
            s = cffi_requests.Session()
            if self.verbose:
                print("[i] HTTP-бэкенд: curl_cffi (impersonate=chrome)", file=sys.stderr)
            return s, True
        if std_requests is None:
            raise SystemExit(
                "Модуль 'requests' не установлен: pip install -r requirements.txt")
        if self.verbose:
            print("[i] HTTP-бэкенд: requests", file=sys.stderr)
        return std_requests.Session(), False

    @property
    def session(self):
        if self._session is None:
            self._session, self._is_cffi = self._make_session()
        return self._session

    @property
    def is_cffi(self) -> bool:
        if self._session is None:
            self._session, self._is_cffi = self._make_session()
        return self._is_cffi

    def _headers(self) -> dict:
        h = {
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/126.0.0.0 Safari/537.36"),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            "Referer": self.base_url + "/",
        }
        h.update(self.extra_headers)
        if self.token:
            h["Authorization"] = "Bearer " + self.token
        return h

    def _throttle(self):
        if self.min_interval > 0:
            wait = self.min_interval - (time.monotonic() - self._last_request_ts)
            if wait > 0:
                time.sleep(wait)
        self._last_request_ts = time.monotonic()

    def _log_request(self, method, url, params, payload):
        if not self.verbose:
            return
        line = f"[i] {method} {url}"
        if params:
            line += " ?" + json.dumps(params, ensure_ascii=False)
        if payload:
            line += " body=" + json.dumps(payload, ensure_ascii=False)[:500]
        print(line, file=sys.stderr)

    # -- публичное ----------------------------------------------------------

    def request(self, method: str, path: str, params=None, json_body=None,
                data=None, headers=None, retries: int | None = None,
                raw: bool = False) -> ApiResponse | tuple:
        """Запрос к API. Возвращает ApiResponse (или (status, headers, text)
        при raw=True — для скачивания JS-бандлов)."""
        from urllib.parse import urljoin

        if path.startswith("http://") or path.startswith("https://"):
            url = path
        else:
            url = urljoin(self.base_url + "/", path.lstrip("/"))

        method = method.upper()
        max_tries = self.retries if retries is None else retries
        if method in ("POST", "PUT", "PATCH", "DELETE"):
            max_tries = min(max_tries, 1)  # мутации не ретраим агрессивно
        last_err: Exception | None = None

        for attempt in range(max_tries + 1):
            self._throttle()
            self._log_request(method, url, params, json_body if json_body is not None else data)
            try:
                merged_headers = self._headers()
                merged_headers.update(headers or {})
                kwargs = dict(
                    method=method, url=url, params=params,
                    headers=merged_headers, timeout=self.timeout,
                    verify=not self.insecure, allow_redirects=True)
                if json_body is not None:
                    kwargs["json"] = json_body
                if data is not None:
                    kwargs["data"] = data
                if self.proxy:
                    kwargs["proxies"] = {"http": self.proxy, "https": self.proxy}
                if self.is_cffi:
                    kwargs["impersonate"] = "chrome"
                resp = self.session.request(**kwargs)
                if raw:
                    return resp.status_code, dict(resp.headers), resp.text
                parsed = ApiResponse(resp.status_code, url, resp.headers, resp.text)
                if resp.status_code in (429, 500, 502, 503, 504) and attempt < max_tries:
                    retry_after = resp.headers.get("Retry-After")
                    delay = float(retry_after) if retry_after and retry_after.isdigit() \
                        else 0.5 * (2 ** attempt)
                    if self.verbose:
                        print(f"[i] {resp.status_code} — повтор через {delay:.1f}s",
                              file=sys.stderr)
                    time.sleep(delay)
                    continue
                return parsed
            except Exception as e:  # сетевые ошибки
                last_err = e
                if attempt < max_tries:
                    time.sleep(0.5 * (2 ** attempt))
                    continue
        raise SystemExit(f"Сетевая ошибка: {last_err}. "
                         f"Проверьте доступ к {self.base_url} из вашей сети.")

    def get(self, path, **kw) -> ApiResponse:
        return self.request("GET", path, **kw)

    def post(self, path, **kw) -> ApiResponse:
        return self.request("POST", path, **kw)

    def put(self, path, **kw) -> ApiResponse:
        return self.request("PUT", path, **kw)

    def patch(self, path, **kw) -> ApiResponse:
        return self.request("PATCH", path, **kw)

    def delete(self, path, **kw) -> ApiResponse:
        return self.request("DELETE", path, **kw)


# --------------------------------------------------------------------------
# Сессия (auth)
# --------------------------------------------------------------------------

LOGIN_PATH_DEFAULTS = [
    "/api/v1/auth/login", "/api/v1/login", "/api/v1/auth/signin",
    "/api/auth/login", "/api/login", "/api/v1/session", "/api/v1/sessions",
    "/api/v1/auth/token", "/api/v1/token",
]

ME_PATH_DEFAULTS = [
    "/api/v1/auth/me", "/api/v1/me", "/api/v1/users/me",
    "/api/v1/auth/profile", "/api/v1/profile", "/api/v1/auth/current",
]


class Auth:
    def __init__(self, client: Client, cfg: Path | None = None):
        self.client = client
        self.cfg_dir = cfg or config_dir()
        self.session_file = self.cfg_dir / "session.json"
        self.session: dict = {}

    # -- хранение ------------------------------------------------------------

    def load(self) -> dict:
        if self.session_file.exists():
            try:
                self.session = json.loads(self.session_file.read_text("utf-8"))
                if self.session.get("token"):
                    self.client.token = self.session["token"]
            except (OSError, ValueError):
                self.session = {}
        return self.session

    def save(self) -> None:
        self.cfg_dir.mkdir(parents=True, exist_ok=True)
        self.session_file.write_text(
            json.dumps(self.session, ensure_ascii=False, indent=2), "utf-8")
        ensure_private(self.session_file)

    def clear(self) -> None:
        self.session = {}
        self.client.token = ""
        if self.session_file.exists():
            self.session_file.unlink()

    # -- вход ------------------------------------------------------------------

    def login_candidates(self, api_map: dict) -> list:
        cands: list = []
        for ep in (api_map.get("endpoints") or []):
            p = ep.get("path", "")
            if LOGIN_PATH_HINT_RE.search(p) and p not in cands:
                cands.append(p)
        cands += [p for p in LOGIN_PATH_DEFAULTS if p not in cands]
        return cands

    def me_candidates(self, api_map: dict) -> list:
        cands: list = []
        for ep in (api_map.get("endpoints") or []):
            p = ep.get("path", "")
            if ME_PATH_HINT_RE.search(p) and p not in cands:
                cands.append(p)
        cands += [p for p in ME_PATH_DEFAULTS if p not in cands]
        return cands

    def login(self, email: str, password: str, api_map: dict | None = None,
              login_field: str = "email", verbose: bool = False) -> dict:
        api_map = api_map or {}
        tried: list = []
        last_error: str = ""
        for path in self.login_candidates(api_map):
            tried.append(path)
            body = {login_field: email, "password": password}
            try:
                resp = self.client.post(path, json_body=body, retries=0)
            except SystemExit as e:
                last_error = str(e)
                if verbose:
                    print(f"[i] {path}: {last_error}", file=sys.stderr)
                continue
            if resp.status in (404, 405, 501) and \
                    (resp.json or {}).get("error", {}).get("code") == "NOT_FOUND":
                if verbose:
                    print(f"[i] {path}: нет такого эндпоинта", file=sys.stderr)
                continue
            # Маршрут существует — останавливаемся на нём, что бы ни ответил.
            tokens = find_token(resp.json)
            if tokens:
                tokens.sort(key=lambda t: (t[0], t[1]))
                priority, key_path, value = tokens[0]
                self.session = {
                    "base_url": self.client.base_url,
                    "email": email,
                    "login_endpoint": path,
                    "login_field": login_field,
                    "token_path": key_path,
                    "token": value,
                    "saved_at": now_iso(),
                }
                self.client.token = value
                self.save()
                print(f"[ok] Вход выполнен через {path}; токен найден в поле "
                      f"'{key_path}' и сохранён в {self.session_file}")
                return self.session
            err = (resp.json or {})
            code = (err.get("error") or {}).get("code") or f"HTTP_{resp.status}"
            message = (err.get("error") or {}).get("message") or resp.body[:200]
            last_error = f"{path}: [{code}] {message}"
            if verbose:
                print(f"[i] {last_error}", file=sys.stderr)
            break  # маршрут существует — дальше перебирать смысла нет
        raise SystemExit(
            f"Не удалось войти. Проверенные эндпоинты: {', '.join(tried)}\n"
            f"Последний ответ: {last_error or 'нет ответа'}")

    def me(self, api_map: dict | None = None) -> dict | None:
        """Запрос 'кто я' для проверки токена. Возвращает данные пользователя."""
        api_map = api_map or {}
        if not self.client.token:
            return None
        for path in self.me_candidates(api_map):
            try:
                resp = self.client.get(path, retries=0)
            except SystemExit:
                continue
            if resp.status in (404, 405) and \
                    (resp.json or {}).get("error", {}).get("code") == "NOT_FOUND":
                continue
            if resp.ok:
                data = resp.unwrap()
                if isinstance(data, dict) or isinstance(data, list):
                    return data
                return {"raw": data}
            if resp.status in (401, 403):
                raise ApiError("UNAUTHORIZED", "Токен недействителен",
                               resp.status, resp.url)
        return None


# --------------------------------------------------------------------------
# Разведка API (recon)
# --------------------------------------------------------------------------

class Recon:
    """Скачивает страницу входа и JS-бандлы, извлекает эндпоинты API."""

    def __init__(self, client: Client, cfg: Path | None = None, verbose: bool = False):
        self.client = client
        self.cfg_dir = cfg or config_dir()
        self.verbose = verbose
        self.cache = self.cfg_dir / "bundles"
        self.map_file = self.cfg_dir / "api_map.json"

    # -- получение HTML и бандлов ----------------------------------------------

    def fetch_index(self) -> tuple:
        """Возвращает (html, url) страницы входа."""
        last_html, last_url = "", ""
        for path in ("/login", "/"):
            status, headers, text = self.client.request(
                "GET", path, raw=True, retries=1)
            if status == 200 and text.strip():
                if "<html" in text.lower() or "<script" in text.lower() \
                        or "<!doctype" in text.lower():
                    return text, self.client.base_url + path
                last_html, last_url = text, self.client.base_url + path
        if last_html:
            return last_html, last_url
        raise SystemExit("Не удалось получить HTML страницы входа "
                         f"с {self.client.base_url}")

    @staticmethod
    def extract_asset_urls(html: str, page_url: str) -> list:
        from urllib.parse import urljoin

        urls: list = []
        for m in re.finditer(r"""<script[^>]+src=["']([^"']+)["']""", html, re.I):
            urls.append(urljoin(page_url, m.group(1)))
        for m in re.finditer(
                r"""<link[^>]+rel=["']modulepreload["'][^>]+href=["']([^"']+)["']""",
                html, re.I):
            urls.append(urljoin(page_url, m.group(1)))
        # inline import в Vite/Remix иногда в <script type="module">import"..."
        for m in re.finditer(
                r"""<script[^>]*type=["']module["'][^>]*>\s*import\s*["']([^"']+)["']""",
                html, re.I):
            urls.append(urljoin(page_url, m.group(1)))
        seen: list = []
        for u in urls:
            if u not in seen:
                seen.append(u)
        return seen

    def download_assets(self, urls: list) -> dict:
        """Возвращает {url: содержимое} для JS-файлов."""
        self.cache.mkdir(parents=True, exist_ok=True)
        assets: dict = {}
        for u in urls:
            name = re.sub(r"[^A-Za-z0-9_.-]", "_", u.split("?")[0])
            local = self.cache / name
            try:
                status, headers, text = self.client.request("GET", u, raw=True,
                                                            retries=1)
            except SystemExit as e:
                if self.verbose:
                    print(f"[i] {u}: {e}", file=sys.stderr)
                continue
            if status != 200 or not text:
                if self.verbose:
                    print(f"[i] {u}: status={status}, пропуск", file=sys.stderr)
                continue
            try:
                local.write_text(text, "utf-8", errors="replace")
            except OSError:
                pass
            assets[u] = text
            if self.verbose:
                print(f"[i] скачан {u} ({len(text)} байт) -> {local}",
                      file=sys.stderr)
        return assets

    # -- анализ бандлов ----------------------------------------------------------

    def analyze(self, assets: dict) -> dict:
        endpoints: dict = {}   # (method, path) -> {"count": n, "sources": set}
        baseurls: set = set()

        for url, text in assets.items():
            for m in BASEURL_RE.finditer(text):
                baseurls.add(m.group(1))
            for m in PATH_LITERAL_RE.finditer(text):
                path = m.group(1)
                if path == "/" or len(path) < 3:
                    continue
                if STATIC_EXT_RE.search(path):
                    continue
                if not self._score(path):
                    continue
                start, end = m.span()
                before = text[max(0, start - 200):start]
                after = text[end:end + 220]
                method = self._detect_method(before, after)
                key = (method or "?", path)
                ep = endpoints.setdefault(key, {"count": 0, "sources": set()})
                ep["count"] += 1
                ep["sources"].add(url.split("/")[-1])

        result = []
        base_hint_set = {b.rstrip("/") for b in baseurls}
        for (method, path), info in endpoints.items():
            if path.rstrip("/") in base_hint_set:
                continue  # это базовый URL (напр. "/api/v1"), а не эндпоинт
            result.append({
                "method": method, "path": path,
                "count": info["count"],
                "sources": sorted(info["sources"]),
            })
        # сортировка: сначала явные методы, потом по количеству упоминаний
        result.sort(key=lambda e: (
            0 if e["method"] != "?" else 1,
            -(e["count"]),
            e["path"]))
        return {
            "generated_at": now_iso(),
            "base_url": self.client.base_url,
            "assets": sorted(assets.keys()),
            "api_base_hints": sorted(baseurls),
            "endpoints": result,
        }

    @staticmethod
    def _score(path: str) -> int:
        low = path.lower()
        s = 0
        if "api" in low:
            s += 3
        if re.search(r"/v\d+(/|$)", low):
            s += 2
        segs = set(re.split(r"[/:{}?]", low))
        if segs & set(RESOURCE_WORDS):
            s += 2
        if low.count("/") >= 2:
            s += 1
        if path.startswith("/assets/") or path.startswith("/static/"):
            s -= 2
        return s

    @staticmethod
    def _detect_method(before: str, after: str) -> str:
        m = METHOD_BEFORE_RE.search(before)
        if m:
            for g in m.groups():
                if g:
                    return g.upper()
        m = METHOD_AFTER_RE.search(after)
        if m:
            return (m.group(1) or "").upper()
        return "?"

    def save_map(self, api_map: dict) -> None:
        self.cfg_dir.mkdir(parents=True, exist_ok=True)
        self.map_file.write_text(
            json.dumps(api_map, ensure_ascii=False, indent=2), "utf-8")

    def load_map(self) -> dict:
        try:
            return json.loads(self.map_file.read_text("utf-8"))
        except (OSError, ValueError):
            return {}

    def run(self, save_assets: bool = True) -> dict:
        print(f"[*] Получаем страницу входа с {self.client.base_url} ...")
        html, page_url = self.fetch_index()
        urls = self.extract_asset_urls(html, page_url)
        if not urls:
            print("[!] В HTML не найдено <script src=...>. Возможно, это SSR-"
                  "приложение (Remix/Next) — тогда эндпоинты лучше снять через "
                  "DevTools: F12 -> Network -> XHR, и использовать команду 'call'.")
            api_map = {"generated_at": now_iso(), "base_url": self.client.base_url,
                       "assets": [], "api_base_hints": [], "endpoints": []}
            self.save_map(api_map)
            return api_map
        print(f"[*] Найдено файлов: {len(urls)}")
        assets = self.download_assets(urls)
        print(f"[*] Скачано JS-файлов: {len(assets)}")
        api_map = self.analyze(assets)
        self.save_map(api_map)
        print(f"[*] Карта API сохранена: {self.map_file}")
        return api_map


# --------------------------------------------------------------------------
# Операции с ресурсами
# --------------------------------------------------------------------------

DEFAULT_API_PREFIX = "/api/v1"


def load_api_map(cfg: Path | None = None) -> dict:
    cfg = cfg or config_dir()
    try:
        return json.loads((cfg / "api_map.json").read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def resolve_resource_path(resource: str, api_map: dict) -> str:
    """Превращает 'requests' в путь API, используя карту эндпоинтов."""
    if resource.startswith("/") or resource.startswith("http"):
        return resource
    r = resource.strip("/").lower()
    best, best_score = None, -1
    for ep in (api_map.get("endpoints") or []):
        path = ep["path"]
        segs = [s for s in re.split(r"[/{}:$?]+", path) if s]
        plain = [s for s in segs if not s.startswith("{") and not s.startswith(":")]
        score = 0
        if plain and plain[-1].lower() == r:
            score = 3
        elif r in [s.lower() for s in plain]:
            score = 2
        elif any(s.lower().startswith(r) for s in plain):
            score = 1
        if score > best_score:
            best, best_score = path, score
    return best or f"{DEFAULT_API_PREFIX}/{resource}"


_ID_TEMPLATE_RE = re.compile(
    r"\$\{[^}]*id[^}]*\}|(?::|\{)[A-Za-z0-9_]*id[A-Za-z0-9_]*(?:\}|(?=/|$))")
_ANY_TEMPLATE_RE = re.compile(r"\$\{[^}]*\}|(?::|\{)[A-Za-z0-9_]+(?:\}|(?=/|$))")


def inject_id(path: str, record_id) -> str:
    if record_id is None:
        return path
    rid = str(record_id)
    m = _ID_TEMPLATE_RE.search(path)
    if m:
        return path.replace(m.group(0), rid)
    m = _ANY_TEMPLATE_RE.search(path)
    if m:
        return path.replace(m.group(0), rid)
    return path.rstrip("/") + "/" + rid


def substitute_params(path: str, mapping: dict) -> str:
    """Подставляет {name}, :name и ${name} в шаблон пути."""
    for name, value in mapping.items():
        path = re.sub(r"\$\{" + re.escape(name) + r"\}", str(value), path)
        path = re.sub(r"\{" + re.escape(name) + r"\}", str(value), path)
        path = re.sub(r":" + re.escape(name) + r"(?=/|$)", str(value), path)
    return path


def build_query(query_args: list) -> dict:
    params = {}
    for q in query_args or []:
        if "=" in q:
            k, v = q.split("=", 1)
            params[k.strip()] = smart_value(v)
        else:
            params[q.strip()] = ""
    return params


def parse_body(args: list) -> dict | None:
    """-d '{"a":1}' | -d @file.json | -d key=value ... -> dict"""
    if not args:
        return None
    body: dict = {}
    for a in args:
        if a.startswith("@"):
            raw = Path(a[1:]).read_text("utf-8")
            body.update(json.loads(raw))
        elif a.lstrip().startswith("{") or a.lstrip().startswith("["):
            loaded = json.loads(a)
            if isinstance(loaded, dict):
                body.update(loaded)
            else:
                body = {"_raw": loaded}
        elif "=" in a:
            k, v = a.split("=", 1)
            body[k.strip()] = smart_value(v)
        else:
            raise SystemExit(f"Не понял аргумент данных: {a!r} "
                             "(ожидается JSON, @file.json или key=value)")
    return body or None


def fetch_all_pages(client: Client, path: str, query: dict | None,
                    limit: int, verbose: bool) -> list:
    """Выкачивает все страницы ресурса с автоопределением пагинации."""
    rows: list = []
    page = 1
    params = dict(query or {})
    params.update({"page": page, "limit": limit})
    while True:
        resp = client.get(path, params=params)
        batch = resp.as_list()
        rows.extend(batch)
        hint = resp.pagination_hint()
        if verbose:
            print(f"[i] {path} page={page}: +{len(batch)} записей", file=sys.stderr)
        if len(batch) < limit:
            break
        if hint.get("total") is not None and len(rows) >= int(hint["total"]):
            break
        page += 1
        params["page"] = page
        if page > 10000:  # защита от бесконечного цикла
            print("[!] Достигнут лимит 10000 страниц — прерываю", file=sys.stderr)
            break
    return rows


def export_rows(rows: list, csv_file: str | None, json_file: str | None) -> None:
    if csv_file:
        flat = [flatten_dict(r) if isinstance(r, dict) else {"value": r}
                for r in rows]
        keys: list = []
        for r in flat:
            for k in r:
                if k not in keys:
                    keys.append(k)
        with open(csv_file, "w", newline="", encoding="utf-8-sig") as f:
            w = csv_mod.DictWriter(f, fieldnames=keys)
            w.writeheader()
            for r in flat:
                w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in keys})
        print(f"[ok] CSV сохранён: {csv_file} ({len(rows)} записей)")
    if json_file:
        with open(json_file, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=2)
        print(f"[ok] JSON сохранён: {json_file} ({len(rows)} записей)")


def watch_resource(client: Client, path: str, query: dict, limit: int,
                   interval: float, once: bool, events_file: str | None,
                   state_file: Path, verbose: bool) -> None:
    state: dict = {}
    if state_file.exists():
        try:
            state = json.loads(state_file.read_text("utf-8"))
        except (OSError, ValueError):
            state = {}

    def snapshot() -> dict:
        rows = fetch_all_pages(client, path, query, limit, verbose)
        snap = {}
        for r in rows:
            if isinstance(r, dict):
                rid = r.get("id") or r.get("ID") or r.get("uuid")
                key = str(rid) if rid is not None else \
                    hashlib.md5(json.dumps(r, sort_keys=True,
                                           ensure_ascii=False,
                                           default=str).encode()).hexdigest()
            else:
                key = hashlib.md5(str(r).encode()).hexdigest()
            snap[key] = r
        return snap

    def emit(event: str, key: str, record) -> None:
        ts = now_iso()
        line = f"{ts} {event} id={key}"
        print(line)
        if events_file:
            with open(events_file, "a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": ts, "event": event, "id": key,
                                    "record": record}, ensure_ascii=False,
                                   default=str) + "\n")

    print(f"[*] Наблюдаю за {path} (интервал {interval}s, Ctrl+C — выход)")
    prev = state.get("records", {})
    first_run = not state
    while True:
        try:
            cur = snapshot()
        except SystemExit as e:
            print(f"[!] {e}", file=sys.stderr)
            cur = prev
        if first_run:
            print(f"[*] Первичный снимок: {len(cur)} записей")
            first_run = False
        else:
            for k in sorted(set(cur) - set(prev)):
                emit("NEW", k, cur.get(k))
            for k in sorted(set(prev) - set(cur)):
                emit("GONE", k, None)
            for k in sorted(set(cur) & set(prev)):
                if json.dumps(prev.get(k), sort_keys=True, ensure_ascii=False,
                              default=str) != json.dumps(cur.get(k), sort_keys=True,
                                                        ensure_ascii=False,
                                                        default=str):
                    emit("CHANGED", k, cur.get(k))
        prev = cur
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps({"records": prev, "updated_at": now_iso()},
                                         ensure_ascii=False, default=str), "utf-8")
        if once:
            break
        time.sleep(max(0.5, interval))


def import_csv(client: Client, path: str, csv_file: str, mode: str,
               key: str | None, dry_run: bool, limit: int,
               verbose: bool) -> None:
    with open(csv_file, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv_mod.DictReader(f)
        rows = [r for r in reader]
    if not rows:
        raise SystemExit(f"CSV пуст: {csv_file}")
    if mode == "update" and not key:
        raise SystemExit("Для режима update нужен --key (поле-идентификатор)")
    print(f"[*] {mode} {path}: {len(rows)} записей" +
          (" (dry-run)" if dry_run else ""))
    ok_count, err_count = 0, 0
    for i, row in enumerate(rows):
        payload = {k: smart_value(v) for k, v in row.items() if v not in ("", None)}
        rid = payload.pop(key, None) if key else None
        target = inject_id(path, rid) if mode == "update" else path
        if dry_run:
            print(f"  [{mode.upper()}] {target} {json.dumps(payload, ensure_ascii=False)[:200]}")
            ok_count += 1
            continue
        try:
            if mode == "create":
                resp = client.post(target, json_body=payload)
            else:
                resp = client.put(target, json_body=payload) \
                    if mode == "update" else client.patch(target, json_body=payload)
            resp.unwrap()
            ok_count += 1
            if verbose:
                print(f"  [ok] {i+1}: {target}")
        except (ApiError, SystemExit) as e:
            err_count += 1
            print(f"  [err] {i+1}: {target}: {e}", file=sys.stderr)
        if limit and (i + 1) >= limit:
            print(f"[i] Остановлено по --limit {limit}")
            break
    print(f"[*] Готово: {ok_count} ok, {err_count} ошибок")


# --------------------------------------------------------------------------
# Пользовательские процессы
# --------------------------------------------------------------------------

def run_process(script: str, ctx: dict) -> None:
    path = Path(script)
    if not path.exists():
        raise SystemExit(f"Файл процесса не найден: {path}")
    code = path.read_text("utf-8")
    g = {
        "__name__": "__main__",
        "__file__": str(path.resolve()),
        "api": ctx["client"],
        "client": ctx["client"],
        "auth": ctx["auth"],
        "api_map": ctx["api_map"],
        "ApiError": ApiError,
        "print_table": print_table,
        "now_iso": now_iso,
    }
    try:
        exec(compile(code, str(path), "exec"), g)
    except SystemExit:
        raise
    except Exception as e:
        raise SystemExit(f"Процесс {path} завершился с ошибкой: {e!r}")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="f2c_inventory.py",
        description="CLI для автоматизации работы с inventory.f2c.ru",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
        Примеры:
          f2c_inventory.py recon
          f2c_inventory.py login
          f2c_inventory.py list requests --all --csv requests.csv
          f2c_inventory.py create requests -d '{"title":"Ноутбук","type":1}'
          f2c_inventory.py update requests 42 -d status=done
          f2c_inventory.py call POST /api/v1/requests -d '{"title":"x"}'
          f2c_inventory.py watch requests --interval 60
          f2c_inventory.py run examples/notify_new_requests.py

        Переменные окружения:
          F2C_BASE_URL, F2C_EMAIL, F2C_PASSWORD, F2C_TOKEN,
          F2C_TIMEOUT, F2C_PROXY, F2C_CONFIG_DIR
        """))
    p.add_argument("--base-url", default=os.environ.get("F2C_BASE_URL", DEFAULT_BASE_URL))
    p.add_argument("--timeout", type=float, default=float(os.environ.get("F2C_TIMEOUT", "30")))
    p.add_argument("--proxy", default=os.environ.get("F2C_PROXY", ""))
    p.add_argument("--backend", choices=("auto", "requests", "cffi"), default="auto")
    p.add_argument("--insecure", action="store_true",
                   help="не проверять TLS-сертификат (только если очень нужно)")
    p.add_argument("--min-interval", type=float, default=0.0,
                   help="пауза между запросами, сек (бережно к серверу)")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--version", action="version", version=VERSION)

    sub = p.add_subparsers(dest="command", required=True)

    def add_common(sp, with_id=False):
        sp.add_argument("resource", help="имя ресурса (requests, organizations, ...) "
                                         "или путь /api/...")
        if with_id:
            sp.add_argument("record_id", nargs="?", default=None, help="ID записи")

    sp = sub.add_parser("recon", help="скачать JS-бандлы и составить карту API")
    sp.add_argument("--no-save-assets", action="store_true")

    sp = sub.add_parser("login", help="войти и сохранить токен")
    sp.add_argument("--email", default=os.environ.get("F2C_EMAIL", ""))
    sp.add_argument("--password", default=os.environ.get("F2C_PASSWORD", ""),
                    help="если не указан — спросит интерактивно")
    sp.add_argument("--login-field", default="email",
                    help="имя поля логина (email, login, username)")

    sp = sub.add_parser("logout", help="забыть сохранённый токен")
    sp = sub.add_parser("status", help="проверить сессию")
    sp = sub.add_parser("api-map", help="показать карту эндпоинтов")
    sp.add_argument("--filter", default="", help="подстрока для фильтра путей")
    sp = sub.add_parser("health", help="проверить доступность API")

    sp = sub.add_parser("call", help="произвольный запрос")
    sp.add_argument("method", choices=("GET", "POST", "PUT", "PATCH", "DELETE"))
    sp.add_argument("path")
    sp.add_argument("-d", "--data", action="append", default=[])
    sp.add_argument("-q", "--query", action="append", default=[])
    sp.add_argument("--param", action="append", default=[], metavar="NAME=VALUE",
                    help="подстановка в шаблон пути {name}")
    sp.add_argument("--raw", action="store_true", help="показать сырой ответ")
    sp.add_argument("--out", default="", help="сохранить ответ в файл")

    sp = sub.add_parser("list", help="список записей ресурса")
    add_common(sp)
    sp.add_argument("-q", "--query", action="append", default=[])
    sp.add_argument("--all", action="store_true", help="выкачать все страницы")
    sp.add_argument("--limit", type=int, default=100, help="размер страницы")
    sp.add_argument("--csv", default="", help="сохранить в CSV")
    sp.add_argument("--json", dest="json_out", default="", help="сохранить в JSON")
    sp.add_argument("--count", action="store_true", help="только количество")

    sp = sub.add_parser("get", help="одна запись")
    add_common(sp, with_id=True)
    sp.add_argument("--raw", action="store_true")

    sp = sub.add_parser("create", help="создать запись")
    add_common(sp)
    sp.add_argument("-d", "--data", action="append", required=True, default=[])
    sp.add_argument("--dry-run", action="store_true")

    sp = sub.add_parser("update", help="обновить запись")
    add_common(sp, with_id=True)
    sp.add_argument("-d", "--data", action="append", required=True, default=[])
    sp.add_argument("--dry-run", action="store_true")

    sp = sub.add_parser("delete", help="удалить запись")
    add_common(sp, with_id=True)
    sp.add_argument("--yes", action="store_true", help="без подтверждения")

    sp = sub.add_parser("export", help="выгрузка ресурса в CSV/JSON")
    add_common(sp)
    sp.add_argument("-q", "--query", action="append", default=[])
    sp.add_argument("--csv", default="", help="файл CSV")
    sp.add_argument("--json", dest="json_out", default="", help="файл JSON")
    sp.add_argument("--limit", type=int, default=100)

    sp = sub.add_parser("import", help="массовое создание/обновление из CSV")
    add_common(sp)
    sp.add_argument("--csv", required=True, help="файл CSV (первая строка — поля)")
    sp.add_argument("--mode", choices=("create", "update", "patch"), default="create")
    sp.add_argument("--key", default="", help="поле-идентификатор для update")
    sp.add_argument("--limit", type=int, default=0, help="максимум строк")
    sp.add_argument("--dry-run", action="store_true")

    sp = sub.add_parser("watch", help="следить за изменениями ресурса")
    add_common(sp)
    sp.add_argument("-q", "--query", action="append", default=[])
    sp.add_argument("--interval", type=float, default=60.0)
    sp.add_argument("--once", action="store_true", help="один снимок и сравнение")
    sp.add_argument("--events", default="", help="дописывать события в JSONL-файл")
    sp.add_argument("--limit", type=int, default=100)

    sp = sub.add_parser("run", help="выполнить пользовательский процесс")
    sp.add_argument("script", help="путь к .py файлу")
    sp.add_argument("args", nargs=argparse.REMAINDER)
    return p


def ensure_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def main(argv=None) -> int:
    ensure_utf8()
    args = build_parser().parse_args(argv)

    cfg = config_dir()
    api_map = load_api_map(cfg)

    auth = Auth(Client(), cfg)
    saved = auth.load()
    token = os.environ.get("F2C_TOKEN", "") or saved.get("token", "")

    client = Client(
        base_url=args.base_url,
        timeout=args.timeout,
        verbose=args.verbose,
        insecure=args.insecure,
        proxy=args.proxy,
        backend=args.backend,
        min_interval=args.min_interval,
        token=token,
    )
    auth.client = client

    cmd = args.command

    if cmd == "recon":
        recon = Recon(client, cfg, verbose=args.verbose)
        api_map = recon.run(save_assets=not args.no_save_assets)
        print(f"\nНайдено эндпоинтов: {len(api_map.get('endpoints', []))}")
        for ep in api_map.get("endpoints", []):
            print(f"  {ep['method']:6s} {ep['path']}  (x{ep['count']})")
        if api_map.get("api_base_hints"):
            print("Базовые URL из кода:", ", ".join(api_map["api_base_hints"]))
        return 0

    if cmd == "login":
        email = args.email or input("Рабочая почта: ").strip()
        if not email:
            raise SystemExit("Нужна почта (--email или F2C_EMAIL)")
        password = args.password or getpass.getpass("Пароль: ")
        auth.login(email, password, api_map, args.login_field, verbose=args.verbose)
        return 0

    if cmd == "logout":
        auth.clear()
        print("[ok] Сессия удалена")
        return 0

    if cmd == "status":
        if not client.token:
            print("Сессия не сохранена. Выполните: f2c_inventory.py login")
            return 1
        print(f"Сохранён вход: {saved.get('email', '?')} "
              f"(через {saved.get('login_endpoint', '?')}, "
              f"токен в поле '{saved.get('token_path', '?')}')")
        try:
            me = auth.me(api_map)
        except ApiError as e:
            print(f"[!] Токен недействителен: {e.message}. Повторите login.")
            return 1
        if me:
            print("[ok] Токен работает. Данные пользователя:")
            if isinstance(me, dict):
                print_table([flatten_dict(me)] if isinstance(me, dict) else [{"value": me}])
            else:
                print(json.dumps(me, ensure_ascii=False, indent=2)[:2000])
            return 0
        print("[?] Токен есть, но проверить не удалось (эндпоинт 'me' не найден "
              "в карте API — запустите recon).")
        return 0

    if cmd == "api-map":
        eps = api_map.get("endpoints", [])
        if args.filter:
            eps = [e for e in eps if args.filter.lower() in e["path"].lower()]
        if not eps:
            print("Карта API пуста. Запустите: f2c_inventory.py recon")
            return 1
        print(f"Эндпоинтов: {len(eps)}"
              + (f" (фильтр: {args.filter!r})" if args.filter else ""))
        for e in eps:
            print(f"  {e['method']:6s} {e['path']}  x{e['count']}")
        return 0

    if cmd == "health":
        for path in ("/api", "/api/v1"):
            try:
                resp = client.get(path, retries=0)
            except SystemExit as e:
                print(f"{path}: недоступен ({e})")
                continue
            if resp.status in (404, 405):
                print(f"{path}: сервер отвечает (status {resp.status}, "
                      f"это нормально для несуществующего пути)")
            else:
                print(f"{path}: status {resp.status}")
        return 0

    if cmd == "call":
        path = args.path
        params_map: dict = {}
        for kv in args.param or []:
            if "=" not in kv:
                raise SystemExit(f"--param: ожидается NAME=VALUE, получено {kv!r}")
            name, value = kv.split("=", 1)
            params_map[name.strip()] = value
        path = substitute_params(path, params_map)
        body = parse_body(args.data)
        resp = client.request(args.method, path, params=build_query(args.query),
                              json_body=body)
        print(f"HTTP {resp.status} {resp.url}")
        if args.raw:
            text = resp.body
        else:
            try:
                text = json.dumps(resp.unwrap(), ensure_ascii=False, indent=2)
            except ApiError as e:
                print(f"[err] {e.code}: {e.message}")
                return 1
        if args.out:
            Path(args.out).write_text(text + "\n", "utf-8")
            print(f"[ok] Ответ сохранён: {args.out}")
        else:
            print(text[:100000])
        return 0

    if cmd in ("list", "get", "create", "update", "delete", "export", "import", "watch"):
        path = resolve_resource_path(args.resource, api_map)
        if args.verbose:
            print(f"[i] Ресурс '{args.resource}' -> {path}", file=sys.stderr)

        if cmd == "list":
            query = build_query(args.query)
            if args.all or args.csv or args.json_out or args.count:
                rows = fetch_all_pages(client, path, query, args.limit, args.verbose)
                if args.count:
                    print(len(rows))
                else:
                    export_rows(rows, args.csv, args.json_out)
                    if not args.csv and not args.json_out:
                        print_table(rows[:200])
                return 0
            resp = client.get(path, params=dict(query, page=1, limit=args.limit))
            rows = resp.as_list()
            print(f"Показана страница 1 ({len(rows)} записей; "
                  f"для полной выгрузки добавьте --all или --csv)")
            print_table(rows[:100])
            return 0

        if cmd == "get":
            resp = client.get(inject_id(path, args.record_id))
            if args.raw:
                print(resp.body[:100000])
            else:
                data = resp.unwrap()
                print(json.dumps(data, ensure_ascii=False, indent=2)[:100000])
            return 0

        if cmd == "create":
            body = parse_body(args.data)
            if args.dry_run:
                print(f"[dry-run] POST {path} {json.dumps(body, ensure_ascii=False)}")
                return 0
            resp = client.post(path, json_body=body)
            print(f"HTTP {resp.status}")
            print(json.dumps(resp.unwrap(), ensure_ascii=False, indent=2)[:100000])
            return 0

        if cmd == "update":
            body = parse_body(args.data)
            if args.dry_run:
                print(f"[dry-run] PUT {inject_id(path, args.record_id)} "
                      f"{json.dumps(body, ensure_ascii=False)}")
                return 0
            resp = client.put(inject_id(path, args.record_id), json_body=body)
            print(f"HTTP {resp.status}")
            print(json.dumps(resp.unwrap(), ensure_ascii=False, indent=2)[:100000])
            return 0

        if cmd == "delete":
            target = inject_id(path, args.record_id)
            if not args.yes:
                ans = input(f"Удалить {target}? [y/N] ").strip().lower()
                if ans not in ("y", "yes", "д", "да"):
                    print("Отменено")
                    return 0
            resp = client.delete(target)
            print(f"HTTP {resp.status}")
            if resp.json:
                print(json.dumps(resp.unwrap(), ensure_ascii=False, indent=2)[:100000])
            return 0

        if cmd == "export":
            rows = fetch_all_pages(client, path, build_query(args.query),
                                   args.limit, args.verbose)
            if not args.csv and not args.json_out:
                raise SystemExit("Укажите --csv ИЛИ --json")
            export_rows(rows, args.csv, args.json_out)
            return 0

        if cmd == "import":
            import_csv(client, path, args.csv, args.mode, args.key or None,
                       args.dry_run, args.limit, args.verbose)
            return 0

        if cmd == "watch":
            state_file = cfg / "states" / (re.sub(r"[^A-Za-z0-9_.-]", "_", path) + ".json")
            try:
                watch_resource(client, path, build_query(args.query), args.limit,
                               args.interval, args.once, args.events, state_file,
                               args.verbose)
            except KeyboardInterrupt:
                print("\n[ok] Остановлено")
            return 0

    if cmd == "run":
        ctx = {"client": client, "auth": auth, "api_map": api_map}
        if args.args:
            sys.argv = [args.script] + args.args
        run_process(args.script, ctx)
        return 0

    raise SystemExit(f"Неизвестная команда: {cmd}")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nПрервано")
        sys.exit(130)
    except ApiError as e:
        print(f"[err] {e.code}: {e.message}", file=sys.stderr)
        sys.exit(1)
    except SystemExit as e:
        if isinstance(e.code, str):
            print(f"[!] {e.code}", file=sys.stderr)
            sys.exit(1)
        sys.exit(e.code or 0)
