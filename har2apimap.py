#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
har2apimap.py — анализатор сетевого лога (HAR) для inventory.f2c.ru.

Извлекает из HAR-файла (Chrome DevTools: Network -> Export HAR, либо
chrome-net-export-log.json) реальную схему API:

  * все вызовы XHR/fetch (метод + путь, пути нормализуются в шаблоны {id});
  * эндпоинт входа и имена полей (email/password);
  * поле токена в ответе входа и формат заголовка авторизации;
  * эндпоинт "кто я" (me) для проверки сессии;
  * структуру ответов (обёртка success/error/data, ключ списка, пагинация);
  * примеры запросов/ответов для каждой группы эндпоинтов.

Использование:
    python3 har2apimap.py chrome-net-export-log.json
    python3 har2apimap.py chrome-net-export-log.json --host inventory.f2c.ru \
        --out ~/.config/f2c-inventory/api_map.json
    python3 f2c_inventory.py recon --har chrome-net-export-log.json   # то же, встроено

Результат совместим с f2c_inventory.py (формат api_map.json).
ВНИМАНИЕ: печатаемые примеры маскируются — токены, куки и пароли не
раскрываются полностью.
"""

import argparse
import json
import re
import sys
from pathlib import Path

DEFAULT_HOST = "inventory.f2c.ru"

# ---------- маскирование чувствительных данных --------------------------------

SENSITIVE_HEADERS = {
    "authorization", "cookie", "set-cookie", "x-auth-token", "x-api-key",
    "api-key", "x-csrf-token", "proxy-authorization",
}

SENSITIVE_BODY_KEYS = (
    "password", "passwd", "token", "access_token", "accessToken",
    "refresh_token", "refreshToken", "session", "sessionid", "cookie",
    "secret", "api_key", "apikey",
)


def mask_value(v: str) -> str:
    if not v:
        return v
    if len(v) <= 8:
        return "*" * len(v)
    return v[:3] + "…" + v[-3:]


def mask_headers(headers: list) -> list:
    out = []
    for h in headers or []:
        name = str(h.get("name", ""))
        value = str(h.get("value", ""))
        if name.lower() in SENSITIVE_HEADERS:
            value = mask_value(value)
        out.append({"name": name, "value": value})
    return out


def mask_body(obj):
    """Рекурсивно маскирует чувствительные поля в JSON."""
    if isinstance(obj, dict):
        return {
            k: mask_value(v) if isinstance(v, str) and
            k.lower() in SENSITIVE_BODY_KEYS else mask_body(v)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [mask_body(x) for x in obj]
    return obj


# ---------- распознавание токенов ----------------------------------------------

TOKEN_KEY_PRIORITY = [
    re.compile(r"access[_-]?token", re.I),
    re.compile(r"^token$|auth[_-]?token", re.I),
    re.compile(r"refresh[_-]?token", re.I),
    re.compile(r"jwt", re.I),
    re.compile(r"session", re.I),
    re.compile(r"api[_-]?key", re.I),
]


def find_token(obj, path: str = ""):
    found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            key_path = f"{path}.{k}" if path else str(k)
            if isinstance(v, str) and len(v) >= 4 and v.strip():
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


# ---------- нормализация URL в шаблоны -----------------------------------------

SEGMENT_ID_RE = re.compile(
    r"^[0-9]{1,19}$|"
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$|"
    r"^[0-9a-fA-F]{16,64}$|"
    r"^(0x)?[0-9a-fA-F]{8,12}$")

STATIC_EXT_RE = re.compile(
    r"\.(js|mjs|css|map|png|jpe?g|gif|svg|webp|ico|woff2?|ttf|otf|eot|"
    r"mp3|mp4|webm|pdf|zip|gz)$", re.I)

ME_PATH_RE = re.compile(r"/(me|profile|current|whoami|self)(/|$|\?)", re.I)
LOGIN_PATH_RE = re.compile(r"(login|signin|sign-in|auth)", re.I)


def is_api_call(entry: dict) -> bool:
    url = entry.get("request", {}).get("url", "")
    path = url.split("?", 1)[0]
    if STATIC_EXT_RE.search(path):
        return False
    resp = entry.get("response", {})
    mime = (resp.get("content") or {}).get("mimeType", "")
    if "json" in mime.lower() or "text/plain" in mime.lower():
        if "api" in path or "auth" in path:
            return True
    # XHR без JSON-ответа тоже считаем API-вызовом (DELETE 204 и т.п.)
    resource_type = entry.get("_resourceType", "") or entry.get("resourceType", "")
    if resource_type in ("xhr", "fetch"):
        return True
    return False


def normalize_path(url: str, method: str) -> str:
    from urllib.parse import urlparse
    p = urlparse(url)
    segs = p.path.split("/")
    out = []
    for s in segs:
        if SEGMENT_ID_RE.match(s):
            out.append("{id}")
        else:
            out.append(s)
    path = "/".join(out) or "/"
    if not path.startswith("/"):
        path = "/" + path
    # маркер того, что в шаблоне есть подстановка — для читаемости
    return path


def query_params(url: str) -> dict:
    from urllib.parse import urlparse, parse_qs
    q = parse_qs(urlparse(url).query)
    return {k: v[0] if len(v) == 1 else v for k, v in q.items()}


# ---------- основной анализ ------------------------------------------------------

def analyze_har(har_path: str, host_filter: str = DEFAULT_HOST, verbose: bool = False):
    raw = Path(har_path).read_text("utf-8", errors="replace")
    try:
        har = json.loads(raw)
    except ValueError as e:
        raise SystemExit(f"Не удалось распарсить HAR: {e}")

    entries = (har.get("log") or {}).get("entries") or []
    if not entries:
        raise SystemExit("В HAR нет записей (log.entries пуст)")

    host_filter = host_filter.lower()
    selected = []
    for e in entries:
        url = (e.get("request") or {}).get("url", "")
        from urllib.parse import urlparse
        host = urlparse(url).netloc.lower()
        if host_filter and host_filter not in host:
            continue
        if not is_api_call(e):
            if verbose:
                print(f"[i] пропуск (не API): {url[:120]}", file=sys.stderr)
            continue
        selected.append(e)

    if not selected:
        # фильтр не подошёл — покажем, какие хосты вообще есть в логе
        hosts = sorted({urlparse(e["request"]["url"]).netloc
                        for e in entries if e.get("request", {}).get("url")})
        raise SystemExit(
            f"API-вызовов к {host_filter!r} не найдено. Хосты в логе: {hosts}")

    endpoints: dict = {}       # (method, template) -> info
    resources: dict = {}       # имя ресурса -> инфо
    auth = {"login_endpoint": None, "login_fields": None, "token_field": None,
            "auth_header_template": None, "me_endpoint": None}
    header_candidates: dict = {}

    for e in selected:
        req = e.get("request") or {}
        resp = e.get("response") or {}
        method = (req.get("method") or "GET").upper()
        url = req.get("url", "")
        template = normalize_path(url, method)
        status = resp.get("status")
        req_headers = {h.get("name", "").lower(): h.get("value", "")
                       for h in (req.get("headers") or [])}
        post_data = (req.get("postData") or {})
        body_text = post_data.get("text", "") or post_data.get("params", "") or ""
        try:
            resp_json = json.loads((resp.get("content") or {}).get("text") or "null")
        except ValueError:
            resp_json = None
        body_json = None
        if body_text:
            try:
                body_json = json.loads(body_text)
            except ValueError:
                body_json = None

        key = (method, template)
        ep = endpoints.setdefault(key, {
            "method": method, "path": template, "count": 0,
            "statuses": set(), "request_bodies": [], "response_bodies": [],
            "query_keys": set(), "paginated": False, "list_key": None,
        })
        ep["count"] += 1
        ep["statuses"].add(status)
        for qk in query_params(url):
            ep["query_keys"].add(qk)
        if body_json is not None:
            if not any(json.dumps(b, sort_keys=True) == json.dumps(body_json, sort_keys=True)
                       for b in ep["request_bodies"]):
                ep["request_bodies"].append(body_json)
        if resp_json is not None:
            if not any(json.dumps(b, sort_keys=True) == json.dumps(resp_json, sort_keys=True)
                       for b in ep["response_bodies"]):
                ep["response_bodies"].append(resp_json)

        # --- авторизация ---------------------------------------------------
        for name, value in req_headers.items():
            if name == "authorization" or name.endswith("auth-token") or \
                    name == "x-api-key":
                header_candidates.setdefault(name, set()).add(value)

        # --- вход ------------------------------------------------------------
        if auth["login_endpoint"] is None and method in ("POST", "PUT") and \
                body_json is not None and "password" in json.dumps(body_json).lower():
            if isinstance(body_json, dict):
                auth["login_endpoint"] = template
                auth["login_fields"] = [k for k in body_json.keys()
                                        if k.lower() not in ("password",)]
                if resp_json is not None:
                    tokens = find_token(resp_json)
                    if tokens:
                        tokens.sort(key=lambda t: (t[0], t[1]))
                        auth["token_field"] = tokens[0][1]
                        print(f"[i] Вход: {method} {template}, поля "
                              f"{auth['login_fields']}, токен в '{auth['token_field']}'")

        # --- me -----------------------------------------------------------------
        if auth["me_endpoint"] is None and method == "GET" and status == 200 and \
                ME_PATH_RE.search(template) and resp_json is not None and \
                (req_headers.get("authorization") or req_headers.get("cookie")):
            auth["me_endpoint"] = template
            print(f"[i] Эндпоинт 'кто я': GET {template}")

        # --- списки ресурсов ------------------------------------------------------
        if method == "GET" and resp_json is not None:
            list_key = None
            if isinstance(resp_json, dict):
                for k in ("items", "results", "records", "rows", "list",
                          "content", "entities", "elements"):
                    if isinstance(resp_json.get(k), list):
                        list_key = k
                        break
                if list_key is None and isinstance(resp_json.get("data"), dict):
                    for k in ("items", "results", "records", "rows", "list"):
                        if isinstance(resp_json["data"].get(k), list):
                            list_key = "data." + k
                            break
            if list_key:
                ep["list_key"] = list_key
                segs = [s for s in template.rstrip("/").split("/") if s and
                        not s.startswith("{")]
                if segs:
                    rname = segs[-1]
                    rinfo = resources.setdefault(rname, {
                        "name": rname, "path": template, "list_key": list_key,
                        "paginated": False, "count": 0})
                    rinfo["count"] += 1
                pq = query_params(url)
                if any(k in pq for k in ("page", "offset")) or \
                        any(k in ("per_page", "limit", "size") for k in pq):
                    ep["paginated"] = True
                    if segs:
                        resources.setdefault(segs[-1], {"name": segs[-1],
                            "path": template, "list_key": list_key,
                            "paginated": False, "count": 0})["paginated"] = True

    # --- формат заголовка авторизации -----------------------------------------
    if auth["token_field"] and header_candidates:
        for name in sorted(header_candidates):
            values = header_candidates[name]
            if name == "authorization":
                for v in values:
                    if re.match(r"^(Bearer|Token|Basic)\s+", v, re.I):
                        scheme = v.split()[0].capitalize()
                        auth["auth_header_template"] = \
                            f"Authorization: {scheme} {{token}}"
                        break
                else:
                    auth["auth_header_template"] = "Authorization: {token}"
                break
            elif name == "cookie":
                auth["auth_header_template"] = "Cookie: {token}"
                break
            else:
                auth["auth_header_template"] = f"{name.title()}: {{token}}"
                break

    # --- итоговая карта ----------------------------------------------------------
    ep_list = []
    for (method, template), ep in endpoints.items():
        ep_list.append({
            "method": method, "path": template, "count": ep["count"],
            "statuses": sorted(ep["statuses"]),
            "list_key": ep["list_key"],
            "paginated": ep["paginated"],
            "query_keys": sorted(ep["query_keys"]),
            "request_example": (mask_body(ep["request_bodies"][0])
                                if ep["request_bodies"] else None),
            "response_example": (mask_body(ep["response_bodies"][0])
                                 if ep["response_bodies"] else None),
        })
    ep_list.sort(key=lambda x: (x["method"] == "?", x["path"]))

    from urllib.parse import urlparse
    hosts = sorted({urlparse(e["request"]["url"]).netloc for e in selected})
    return {
        "generated_at": _now_iso(),
        "source": "har",
        "har_file": str(Path(har_path).resolve()),
        "base_url": "https://" + (hosts[0] if hosts else host_filter),
        "hosts_seen": hosts,
        "endpoints": ep_list,
        "auth": auth,
        "resources": {k: v for k, v in sorted(resources.items())},
    }


def _now_iso() -> str:
    import datetime as dt
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


# ---------- отчёт ------------------------------------------------------------------

def print_report(api_map: dict) -> None:
    eps = api_map.get("endpoints") or []
    print(f"\n=== Отчёт по HAR: {api_map.get('har_file')} ===")
    print(f"Хосты: {', '.join(api_map.get('hosts_seen', []))}")
    print(f"Эндпоинтов (уникальных): {len(eps)}\n")

    auth = api_map.get("auth") or {}
    if auth.get("login_endpoint"):
        print("Авторизация:")
        print(f"  вход     : POST {auth['login_endpoint']}")
        print(f"  поля     : {auth.get('login_fields')}")
        print(f"  токен    : поле ответа '{auth.get('token_field')}'")
        print(f"  заголовок: {auth.get('auth_header_template') or 'не определён'}")
    if auth.get("me_endpoint"):
        print(f"  whoami   : GET {auth['me_endpoint']}")
    print()

    print(f"{'METHOD':7s} {'PATH':58s} {'N':>4s}  {'СТАТУСЫ':14s}  ПАГИНАЦИЯ")
    print("-" * 100)
    for e in eps:
        pag = "page/limit" if e.get("paginated") else ("?" if e.get("query_keys") else "—")
        print(f"{e['method']:7s} {e['path'][:58]:58s} {e['count']:4d}  "
              f"{','.join(map(str, e['statuses'])):14s}  {pag}")

    print("\nПримеры запросов/ответов (чувствительные поля замаскированы):")
    for e in eps:
        if e.get("request_example") is not None or e.get("response_example") is not None:
            print(f"\n--- {e['method']} {e['path']} ---")
            if e.get("request_example") is not None:
                print("  body>", json.dumps(e["request_example"], ensure_ascii=False)[:400])
            if e.get("response_example") is not None:
                print("  resp>", json.dumps(e["response_example"], ensure_ascii=False)[:400])
            if e.get("list_key"):
                print(f"  список записей в поле: '{e['list_key']}'")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Извлечение карты API из HAR-лога для f2c_inventory.py")
    ap.add_argument("har", help="путь к HAR/JSON-файлу сетевого лога")
    ap.add_argument("--host", default=DEFAULT_HOST,
                    help=f"фильтр по хосту (по умолчанию {DEFAULT_HOST})")
    ap.add_argument("--out", default="", help="куда сохранить api_map.json")
    ap.add_argument("--no-report", action="store_true", help="не печатать отчёт")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    api_map = analyze_har(args.har, args.host, args.verbose)
    if not args.no_report:
        print_report(api_map)
    out = Path(args.out) if args.out else None
    if out:
        out.write_text(json.dumps(api_map, ensure_ascii=False, indent=2), "utf-8")
        print(f"\n[ok] Карта API сохранена: {out}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit as e:
        if isinstance(e.code, str):
            print(f"[!] {e.code}", file=sys.stderr)
            sys.exit(1)
        sys.exit(e.code or 0)
