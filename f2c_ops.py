#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
f2c_ops.py — доменные операции для inventory.f2c.ru.

Реализует команды (вызываются из f2c_inventory.py):

  addr      — интерактивный поиск и выбор адресов (организация → адрес)
  equipment — список и добавление оборудования
  card      — все операции с карточкой оборудования

Модуль не запускается самостоятельно: он импортируется f2c_inventory.py.
Так как f2c_inventory импортирует f2c_ops в процессе своей инициализации,
доступ к ядру (ApiError, print_table, fetch_all_pages, ...) выполняется
лениво через ссылку _core — атрибуты читаются только в рантайме.

Документация: docs/ADDRESSES.md, docs/EQUIPMENT.md, docs/CARD.md.
"""

import json
import mimetypes
import re
import sys
from pathlib import Path

import f2c_inventory as _core  # noqa: F401 (ленивый доступ к ядру)
from interactive import ask_text, ask_yesno, choose, confirm_payload, is_tty

# --------------------------------------------------------------------------
# Таблица действий: токен в пути -> HTTP-метод по умолчанию.
# Настоящие маршруты системы (из JS-бандла) часто попадают в карту API с
# методом "?", поэтому здесь заданы разумные умолчания.
# --------------------------------------------------------------------------

ACTION_METHODS = {
    "start": "POST", "complete": "POST", "reopen": "POST", "paid": "POST",
    "transition": "POST", "move": "POST", "transfer": "POST",
    "found": "POST", "unfound": "POST", "review": "POST",
    "request-correction": "POST", "assign-self-curator": "POST",
    "curator": "PUT", "administrator": "PUT", "opergroup": "PUT",
    "correction-comment": "PUT", "critical-comment": "PUT", "active": "PUT",
}

# Токены, которые НЕ относятся к карточке оборудования (чтобы не путать
# /equipment/.../found с детальной карточкой при автоподборе эндпоинтов).
DETAIL_EXCLUDE = ("transfer", "found", "unfound", "review", "photos",
                  "serial-lookup", "opergroup-check", "move", "export")

ORG_LIST_PAT = re.compile(r"/organizations$", re.I)
ADDR_PAT = re.compile(r"/organizations/.*/addresses$", re.I)
ADDR_DETAIL_PAT = re.compile(r"/organizations/.*/addresses/", re.I)
EQUIP_LIST_PAT = re.compile(r"/equipment$", re.I)
SERIAL_MANUAL_PAT = re.compile(r"serial-lookup/manual$", re.I)
SERIAL_PHOTO_PAT = re.compile(r"serial-lookup/photo$", re.I)
PHOTOS_PAT = re.compile(r"/photos(/.*)?$", re.I)
HISTORY_PAT = re.compile(r"(journal|history)", re.I)

_TMPL_RE = re.compile(r"\$\{[^}]*\}|:[A-Za-z0-9_]+(?=/|$)|\{[A-Za-z0-9_]+\}")


# --------------------------------------------------------------------------
# Вспомогательные функции
# --------------------------------------------------------------------------

def _expand(path: str) -> list:
    """Варианты пути: с префиксом /api и без него."""
    if path.startswith("/api") or path.startswith("http"):
        return [path]
    return ["/api" + path, path]


def _dedupe(cands):
    seen, out = set(), []
    for m, p in cands:
        key = (m.upper(), p)
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def _map_candidates(api_map, pattern, method=None, prefer_post=False):
    """(method, path) из карты API, отфильтрованные регуляркой по пути."""
    out = []
    for ep in api_map.get("endpoints") or []:
        p = ep.get("path", "")
        if not pattern.search(p):
            continue
        m = ep.get("method") or "?"
        if m == "?":
            m = "POST" if prefer_post else "GET"
        if method:
            m = method
        out.append((m.upper(), p))
    return out


def _inject_last(path: str, rid) -> str:
    """Подставляет id в ПОСЛЕДНИЙ шаблон пути (для /photos/${n} и т.п.)."""
    matches = list(_TMPL_RE.finditer(path))
    if not matches:
        return path.rstrip("/") + "/" + str(rid)
    m = matches[-1]
    return path[:m.start()] + str(rid) + path[m.end():]


def try_candidates(client, candidates, params=None, json_body=None,
                   files=None, verbose=False):
    """Пробует эндпоинты-кандидаты по очереди, возвращает первый рабочий.

    Пропускаются: 404/405/501, ошибка NOT_FOUND и HTML (SPA-фолбэк).
    Возвращает кортеж (ApiResponse, фактический путь).
    """
    tried = []
    for method, tmpl in candidates:
        for p in _expand(tmpl):
            try:
                resp = client.request(method, p, params=params,
                                      json_body=json_body, files=files,
                                      retries=0)
            except SystemExit:
                tried.append(f"{method} {p} (сеть)")
                continue
            hl = {str(k).lower(): str(v) for k, v in (resp.headers or {}).items()}
            j = resp.json
            code = (j or {}).get("error", {}).get("code") \
                if isinstance(j, dict) else None
            if "text/html" in hl.get("content-type", "") \
                    or resp.status in (404, 405, 501) or code == "NOT_FOUND":
                tried.append(f"{method} {p} (HTTP {resp.status})")
                continue
            if verbose:
                print(f"[i] {method} {p} -> HTTP {resp.status}", file=sys.stderr)
            return resp, p
    raise _core.ApiError(
        "NOT_FOUND",
        "Не удалось найти рабочий эндпоинт API. Перепробовано: "
        + "; ".join(tried[-8:])
        + " … Уточните путь (--path) или метод (--method), либо снимите карту "
          "API по HAR: f2c_inventory.py recon --har лог.har",
        404, "")


def _print_result(resp, path, max_len=200000) -> int:
    print(f"HTTP {resp.status} (через {path})")
    try:
        data = resp.unwrap()
    except _core.ApiError as e:
        print(f"[err] {e.code}: {e.message}")
        return 1
    print(json.dumps(data, ensure_ascii=False, indent=2)[:max_len])
    return 0


def _file_payload(file_path: str, field: str = "photo") -> dict:
    p = Path(file_path)
    if not p.exists():
        raise SystemExit(f"Файл не найден: {p}")
    ctype = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
    return {field: (p.name, p.read_bytes(), ctype)}


def _ask_fields() -> dict:
    payload = {}
    print("Вводите поля в формате ключ=значение (пустая строка — готово):")
    while True:
        line = ask_text("поле", default="")
        if not line:
            break
        if "=" not in line:
            print("  ← формат: ключ=значение")
            continue
        k, v = line.split("=", 1)
        payload[k.strip()] = _core.smart_value(v)
    return payload


# --------------------------------------------------------------------------
# Организации и адреса
# --------------------------------------------------------------------------

def list_organizations(client, api_map, verbose=False):
    cands = _dedupe(_map_candidates(api_map, ORG_LIST_PAT, "GET")
                    + [("GET", "/api/organizations"),
                       ("GET", "/api/v1/organizations")])
    resp, path = try_candidates(client, cands, verbose=verbose)
    rows = resp.as_list()
    if verbose:
        print(f"[i] организации: {path} — {len(rows)}", file=sys.stderr)
    return rows


def org_addresses(client, api_map, org_id, verbose=False):
    oid = str(org_id)
    subst = {"e": oid, "orgId": oid, "organization_id": oid,
             "organizationId": oid, "id": oid}
    map_cands = [(m, _core.substitute_params(p, subst))
                 for m, p in _map_candidates(api_map, ADDR_PAT, "GET")]
    cands = _dedupe(map_cands
                    + [("GET", f"/api/organizations/{oid}/addresses"),
                       ("GET", f"/api/v1/organizations/{oid}/addresses")])
    resp, path = try_candidates(client, cands, verbose=verbose)
    rows = resp.as_list()
    if verbose:
        print(f"[i] адреса организации {oid}: {path} — {len(rows)}",
              file=sys.stderr)
    return rows


def collect_addresses(client, api_map, org_filter=None, verbose=False):
    """Все адреса по всем организациям (с полями _org_id/_org_name)."""
    orgs = list_organizations(client, api_map, verbose)
    if org_filter:
        orgs = [o for o in orgs if str(o.get("id")) == str(org_filter)]
    result = []
    for org in orgs:
        oid = org.get("id")
        if oid is None:
            continue
        try:
            addrs = org_addresses(client, api_map, oid, verbose)
        except _core.ApiError as e:
            if verbose:
                print(f"[!] организация {oid}: {e.message}", file=sys.stderr)
            continue
        for a in addrs:
            if isinstance(a, dict):
                a = dict(a)
                a.setdefault("_org_id", oid)
                a.setdefault("_org_name",
                             org.get("name") or org.get("title") or "")
            result.append(a)
    return result


def addr_display(a) -> str:
    if not isinstance(a, dict):
        return str(a)
    rid = a.get("id")
    text = next((str(a[k]) for k in
                 ("full_address", "address", "name", "title") if a.get(k)), "")
    city = a.get("city") or a.get("locality")
    street = a.get("street")
    house = a.get("house") or a.get("building")
    tail = ", ".join(str(x) for x in (city, street) if x)
    if house:
        tail = (tail + ", " if tail else "") + str(house)
    if tail and tail not in text:
        text = (text + " — " if text else "") + tail
    org = a.get("_org_name")
    if org:
        text = (text + "  " if text else "") + f"[{org}]"
    return f"#{rid} {text}".strip()


def score_record(rec, query):
    q = (query or "").strip().lower()
    if not q:
        return 1
    text = json.dumps(rec, ensure_ascii=False).lower()
    s = 0
    if q in text:
        s += 10
    for tok in q.split():
        if tok in text:
            s += 2
    if isinstance(rec, dict):
        for k in ("full_address", "address", "street", "city", "name", "title"):
            v = str(rec.get(k) or "").lower()
            if q in v:
                s += 6
            elif any(t in v for t in q.split()):
                s += 2
    return s


# --------------------------------------------------------------------------
# Оборудование
# --------------------------------------------------------------------------

def equipment_rows(client, api_map, params=None, all_pages=False, limit=100,
                   verbose=False):
    params = dict(params or {})
    cands = _dedupe(_map_candidates(api_map, EQUIP_LIST_PAT, "GET")
                    + [("GET", "/api/equipment"), ("GET", "/api/v1/equipment")])
    resp, path = try_candidates(client, cands, params=params, verbose=verbose)
    if all_pages:
        return _core.fetch_all_pages(client, path, params, limit, verbose)
    return resp.as_list()


def equipment_detail_candidates(api_map, card_id, method="GET"):
    cands = []
    for ep in api_map.get("endpoints") or []:
        p = ep.get("path", "")
        if "equipment" not in p.lower():
            continue
        if any(tok in p.lower() for tok in DETAIL_EXCLUDE):
            continue
        if p.count("${") > 2:
            continue
        m = ep.get("method") or "?"
        if m == "?":
            m = method
        cands.append((m.upper(), _core.inject_id(p, card_id)))
    cands += [(method, f"/api/equipment/{card_id}"),
              (method, f"/api/v1/equipment/{card_id}")]
    return _dedupe(cands)


def card_action(client, api_map, action, card_id, payload=None, files=None,
                inventory_id=None, params=None, method=None, path=None,
                verbose=False):
    """POST/PUT-действие с карточкой: transfer/found/unfound/review/..."""
    default_method = ACTION_METHODS.get(action, "POST")
    cands = []
    if path:
        cands.append((method or default_method, path))
    pat = re.compile(r"/" + re.escape(action) + r"$", re.I)
    cands += _map_candidates(api_map, pat, method or default_method,
                             prefer_post=(default_method == "POST"))
    subst = {}
    if card_id is not None:
        subst.update({"t": str(card_id), "equipmentId": str(card_id),
                      "equipment_id": str(card_id)})
    if inventory_id is not None:
        subst.update({"e": str(inventory_id), "inventoryId": str(inventory_id),
                      "inventory_id": str(inventory_id)})
    final = []
    for m, p in cands:
        p2 = p
        if _TMPL_RE.search(p):
            p2 = _core.substitute_params(p, subst)
            left = _TMPL_RE.findall(p2)
            if inventory_id is None and left:
                # шаблон требует контекста инвентаризации — пропускаем
                continue
            if left and card_id is not None:
                p2 = _core.inject_id(p2, card_id)
        final.append((m, p2))
    if card_id is not None:
        final += [(default_method, f"/api/equipment/{card_id}/{action}"),
                  (default_method, f"/api/equipment/{action}/{card_id}")]
        if inventory_id is not None:
            final.append((default_method, f"/api/equipment/inventories/"
                                          f"{inventory_id}/{card_id}/{action}"))
    return try_candidates(client, _dedupe(final), params=params,
                          json_body=payload, files=files, verbose=verbose)


def _photo_endpoint(client, api_map, card_id, method, path_override,
                    method_override, files=None, verbose=False):
    cands = _map_candidates(api_map, PHOTOS_PAT, method_override or method,
                            prefer_post=(method == "POST"))
    cands = [(m, _inject_last(p, card_id)) for m, p in cands]
    if path_override:
        cands = [(method_override or method, path_override)] + cands
    cands = _dedupe(cands + [(method, f"/api/equipment/{card_id}/photos")])
    return try_candidates(client, cands, files=files, verbose=verbose)


# --------------------------------------------------------------------------
# Команда addr
# --------------------------------------------------------------------------

def cmd_addr(client, auth, api_map, args, cfg):
    sub = args.addr_cmd
    if sub == "search":
        return addr_search(client, api_map, args)
    if sub == "list":
        return addr_list(client, api_map, args)
    if sub == "pick":
        return addr_pick(client, api_map, args)
    if sub == "show":
        return addr_show(client, api_map, args)
    raise SystemExit(f"Неизвестная подкоманда addr: {sub}")


def _show_equipment_at(client, api_map, address_id, verbose=False):
    try:
        rows = equipment_rows(client, api_map,
                              params={"address_id": address_id},
                              verbose=verbose)
    except _core.ApiError as e:
        print(f"  (!) {e.message}")
        return
    if not rows:
        print("  (оборудования на адресе нет)")
        return
    _core.print_table(rows[:200])


def _print_address(a):
    print(addr_display(a))
    print(json.dumps({k: v for k, v in a.items() if not k.startswith("_")},
                     ensure_ascii=False, indent=2)[:8000])


def addr_search(client, api_map, args):
    addrs = collect_addresses(client, api_map, args.org, args.verbose)
    query = args.query or ""
    scored = sorted(((score_record(a, query), a) for a in addrs),
                    key=lambda t: (-t[0], addr_display(t[1]).lower()))
    if query:
        scored = [(s, a) for s, a in scored if s > 0]
    limit = args.limit or len(scored)
    hits = [a for _, a in scored[:limit]]
    if not hits:
        print(f"По запросу {query!r} ничего не найдено "
              f"(проверено адресов: {len(addrs)})")
        return 1
    if args.interactive:
        chosen = choose("Найденные адреса", hits, display=addr_display,
                        search_hint="продолжайте печатать — живой фильтр")
        if chosen is None:
            return 0
        hits = [chosen]
    for a in hits:
        print()
        _print_address(a)
        if args.show_equipment:
            print("\nОборудование на адресе:")
            _show_equipment_at(client, api_map, a.get("id"), args.verbose)
    return 0


def addr_list(client, api_map, args):
    addrs = collect_addresses(client, api_map, args.org, args.verbose)
    if args.csv:
        rows = [_core.flatten_dict(a) if isinstance(a, dict) else {"value": a}
                for a in addrs]
        _core.export_rows(rows, args.csv, None)
        return 0
    rows = []
    for a in addrs:
        if isinstance(a, dict):
            rows.append({"id": a.get("id"),
                         "адрес": addr_display(a),
                         "организация": a.get("_org_name", "")})
        else:
            rows.append({"value": a})
    print(f"Адресов: {len(rows)}")
    _core.print_table(rows[:300])
    return 0


def addr_pick(client, api_map, args):
    orgs = list_organizations(client, api_map, args.verbose)
    if not orgs:
        raise SystemExit("Список организаций пуст (проверьте login/status)")
    if args.org:
        orgs = [o for o in orgs if str(o.get("id")) == str(args.org)]
    org = choose("Организация", orgs,
                 display=lambda o: f"#{o.get('id')} "
                                   f"{o.get('name') or o.get('title') or o}")
    if org is None:
        return 0
    addrs = org_addresses(client, api_map, org.get("id"), args.verbose)
    if not addrs:
        raise SystemExit("У организации нет адресов "
                         "(проверьте эндпоинт в api_map.json)")
    for a in addrs:
        if isinstance(a, dict):
            a.setdefault("_org_name", org.get("name"))
    addr = choose("Адрес", addrs, display=addr_display,
                  search_hint="ввод текста — фильтр адресов")
    if addr is None:
        return 0
    print()
    _print_address(addr)
    if args.show_equipment:
        print("\nОборудование на адресе:")
        _show_equipment_at(client, api_map, addr.get("id"), args.verbose)
    return 0


def addr_show(client, api_map, args):
    aid = str(args.address_id)
    cands = _map_candidates(api_map, ADDR_DETAIL_PAT, "GET")
    cands = [(m, _inject_last(p, aid)) for m, p in cands]
    if args.org:
        cands.append(("GET", f"/api/organizations/{args.org}/addresses/{aid}"))
    cands = _dedupe(cands + [("GET", f"/api/addresses/{aid}"),
                             ("GET", f"/api/v1/addresses/{aid}")])
    resp, path = try_candidates(client, cands, verbose=args.verbose)
    print(f"Адрес #{aid} (источник: {path})")
    print(json.dumps(resp.unwrap(), ensure_ascii=False, indent=2)[:200000])
    if args.equipment:
        print("\nОборудование на адресе:")
        _show_equipment_at(client, api_map, aid, args.verbose)
    return 0


# --------------------------------------------------------------------------
# Команда equipment
# --------------------------------------------------------------------------

def cmd_equipment(client, auth, api_map, args, cfg):
    sub = args.eq_cmd
    if sub == "list":
        params = _core.build_query(args.query)
        if args.address:
            params.setdefault("address_id", args.address)
        if args.inventory:
            params.setdefault("inventory_id", args.inventory)
        all_pages = args.all or bool(args.csv or args.json_out or args.count)
        rows = equipment_rows(client, api_map, params=params,
                              all_pages=all_pages, limit=args.limit,
                              verbose=args.verbose)
        if args.count:
            print(len(rows))
            return 0
        _core.export_rows(rows, args.csv, args.json_out)
        if not args.csv and not args.json_out:
            _core.print_table(rows[:200])
        return 0
    if sub == "add":
        return equipment_add(client, api_map, args)
    if sub == "show":
        return card_show(client, api_map, args.card_id, args.raw)
    raise SystemExit(f"Неизвестная подкоманда equipment: {sub}")


def _wizard_add(client, api_map, args):
    payload = {}
    print("Мастер добавления оборудования")
    orgs = list_organizations(client, api_map, args.verbose)
    if not orgs:
        raise SystemExit("Список организаций пуст (проверьте вход: login)")
    org = choose("Организация", orgs,
                 display=lambda o: f"#{o.get('id')} "
                                   f"{o.get('name') or o.get('title') or o}")
    if org is None:
        raise SystemExit("Отменено")
    addrs = org_addresses(client, api_map, org.get("id"), args.verbose)
    if not addrs:
        raise SystemExit("У организации нет адресов")
    addr = choose("Адрес размещения", addrs, display=addr_display,
                  search_hint="ввод текста — фильтр адресов")
    if addr is None:
        raise SystemExit("Отменено")
    payload["address_id"] = addr.get("id")
    print("\nПоля оборудования (пустое поле пропускается):")
    name = ask_text("Название / модель", required=True)
    if name:
        payload["name"] = name
    for field, label in (("serial_number", "Серийный номер"),
                         ("inventory_number", "Инвентарный номер"),
                         ("quantity", "Количество"),
                         ("comment", "Комментарий")):
        v = ask_text(label)
        if v:
            payload[field] = _core.smart_value(v)
    return payload


def equipment_add(client, api_map, args):
    payload = {}
    if args.data:
        payload = _core.parse_body(args.data) or {}
    if args.interactive or not payload:
        payload.update(_wizard_add(client, api_map, args) or {})
    if args.address and "address_id" not in payload:
        payload["address_id"] = _core.smart_value(args.address)
    if args.inventory and "inventory_id" not in payload:
        payload["inventory_id"] = _core.smart_value(args.inventory)
    if not payload:
        raise SystemExit("Нет данных для создания (-d '{\"name\": \"...\"}')")
    if args.dry_run:
        print("[dry-run] POST /api/equipment")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    if is_tty() and not confirm_payload("Создание оборудования — будет "
                                        "отправлено:", payload):
        print("Отменено")
        return 0
    cands = _dedupe(_map_candidates(api_map, EQUIP_LIST_PAT, "POST",
                                    prefer_post=True)
                    + [("POST", "/api/equipment"),
                       ("POST", "/api/v1/equipment")])
    if args.path:
        cands = [(args.method or "POST", args.path)] + cands
    resp, path = try_candidates(client, cands, json_body=payload,
                                verbose=args.verbose)
    return _print_result(resp, path)


# --------------------------------------------------------------------------
# Команда card — все операции с карточкой оборудования
# --------------------------------------------------------------------------

MENU_ITEMS = [
    ("Показать карточку", "show"),
    ("Редактировать поля", "edit"),
    ("Перенести на другой адрес", "transfer"),
    ("Отметить «найдено»", "found"),
    ("Отметить «не найдено»", "unfound"),
    ("Осмотр (review)", "review"),
    ("Фотографии", "photos"),
    ("Добавить фото", "photo-add"),
    ("Поиск по серийному номеру", "serial"),
    ("Журнал изменений", "history"),
    ("Удалить карточку", "delete"),
    ("Выход", ""),
]


def cmd_card(client, auth, api_map, args, cfg):
    if args.action == "menu":
        return card_menu(client, api_map, args)
    return dispatch_card(client, api_map, args)


def card_menu(client, api_map, args):
    print(f"Карточка #{args.card_id}")
    try:
        card_show(client, api_map, args.card_id)
    except _core.ApiError as e:
        print(f"(!) карточка недоступна: {e.message}")
    while True:
        choice = choose("Действие с карточкой", MENU_ITEMS,
                        display=lambda t: t[0], allow_search=False,
                        search_hint="↑/↓ — выбор · Enter — выполнить")
        if choice is None or not choice[1]:
            print("До встречи!")
            return 0
        args.action = choice[1]
        dispatch_card(client, api_map, args)
        print()


def card_show(client, api_map, card_id, raw=False):
    resp, path = try_candidates(
        client, equipment_detail_candidates(api_map, card_id))
    print(f"Карточка #{card_id} (источник: {path})")
    if raw:
        print(resp.body[:200000])
        return 0
    return _print_result(resp, path)


def dispatch_card(client, api_map, args):
    action = args.action
    card_id = args.card_id
    try:
        if action == "show":
            return card_show(client, api_map, card_id, args.raw)

        if action == "edit":
            payload = _core.parse_body(args.data) or None
            if payload is None:
                payload = _ask_fields()
            if not payload:
                raise SystemExit("Нет данных для редактирования (-d key=value)")
            if args.dry_run:
                print(f"[dry-run] PUT /api/equipment/{card_id}")
                print(json.dumps(payload, ensure_ascii=False, indent=2))
                return 0
            cands = _dedupe(equipment_detail_candidates(api_map, card_id, "PUT")
                            + equipment_detail_candidates(api_map, card_id,
                                                          "PATCH"))
            if args.path:
                cands = [(args.method or "PUT", args.path)] + cands
            resp, path = try_candidates(client, cands, json_body=payload,
                                        verbose=args.verbose)
            return _print_result(resp, path)

        if action == "transfer":
            to_addr = args.to
            payload = _core.parse_body(args.data) or None
            if not to_addr:
                if not is_tty():
                    raise SystemExit("Для transfer нужен --to ID_адреса")
                addrs = collect_addresses(client, api_map, verbose=args.verbose)
                chosen = choose("Куда переносим (адрес-получатель)", addrs,
                                display=addr_display,
                                search_hint="ввод текста — фильтр адресов")
                if chosen is None:
                    raise SystemExit("Отменено")
                to_addr = chosen.get("id")
            if payload is None:
                payload = {"address_id": _core.smart_value(str(to_addr))}
            if args.dry_run:
                print(f"[dry-run] POST transfer #{card_id} -> адрес {to_addr}")
                print(json.dumps(payload, ensure_ascii=False, indent=2))
                return 0
            if is_tty() and not confirm_payload("Перенос оборудования:", payload):
                print("Отменено")
                return 0
            resp, path = card_action(client, api_map, "transfer", card_id,
                                     payload=payload,
                                     inventory_id=args.inventory or None,
                                     method=args.method or None,
                                     path=args.path or None,
                                     verbose=args.verbose)
            return _print_result(resp, path)

        if action in ("found", "unfound"):
            payload = _core.parse_body(args.data) or None
            if args.dry_run:
                print(f"[dry-run] POST {action} #{card_id}"
                      + (f" (инвентаризация {args.inventory})"
                         if args.inventory else ""))
                return 0
            if is_tty() and not confirm_payload(
                    f"Отметить оборудование как «{action}»?", {}):
                print("Отменено")
                return 0
            resp, path = card_action(client, api_map, action, card_id,
                                     payload=payload,
                                     inventory_id=args.inventory or None,
                                     method=args.method or None,
                                     path=args.path or None,
                                     verbose=args.verbose)
            return _print_result(resp, path)

        if action == "review":
            payload = _core.parse_body(args.data)
            if payload is None and args.comment:
                payload = {"comment": args.comment}
            if args.dry_run:
                print(f"[dry-run] POST review #{card_id}")
                print(json.dumps(payload or {}, ensure_ascii=False, indent=2))
                return 0
            resp, path = card_action(client, api_map, "review", card_id,
                                     payload=payload,
                                     inventory_id=args.inventory or None,
                                     method=args.method or None,
                                     path=args.path or None,
                                     verbose=args.verbose)
            return _print_result(resp, path)

        if action == "photos":
            resp, path = _photo_endpoint(client, api_map, card_id, "GET",
                                         args.path, args.method,
                                         verbose=args.verbose)
            return _print_result(resp, path)

        if action == "photo-add":
            if not args.file:
                raise SystemExit("Нужен --file путь_к_фото")
            files = _file_payload(args.file, args.field)
            resp, path = _photo_endpoint(client, api_map, card_id, "POST",
                                         args.path, args.method, files=files,
                                         verbose=args.verbose)
            return _print_result(resp, path)

        if action == "serial":
            number = args.number or ask_text("Серийный номер", required=True)
            payload = _core.parse_body(args.data) or {"serial": str(number)}
            cands = _map_candidates(api_map, SERIAL_MANUAL_PAT, "POST",
                                    prefer_post=True)
            if args.path:
                cands = [(args.method or "POST", args.path)] + cands
            cands = _dedupe(cands + [("POST",
                                      "/api/equipment/serial-lookup/manual")])
            resp, path = try_candidates(client, cands, json_body=payload,
                                        verbose=args.verbose)
            print(f"Поиск по серийному {number!r}:")
            return _print_result(resp, path)

        if action == "serial-photo":
            if not args.file:
                args.file = ask_text("Путь к фото серийного номера",
                                     required=True)
            files = _file_payload(args.file, args.field)
            cands = _map_candidates(api_map, SERIAL_PHOTO_PAT, "POST",
                                    prefer_post=True)
            if args.path:
                cands = [(args.method or "POST", args.path)] + cands
            cands = _dedupe(cands + [("POST",
                                      "/api/equipment/serial-lookup/photo")])
            resp, path = try_candidates(client, cands, files=files,
                                        verbose=args.verbose)
            print(f"Распознавание серийного номера по фото {args.file}:")
            return _print_result(resp, path)

        if action == "history":
            cands = [("GET", f"/api/equipment/{card_id}/history")]
            if args.path:
                cands = [(args.method or "GET", args.path)] + cands
            cands = _dedupe(cands + _map_candidates(api_map, HISTORY_PAT, "GET")
                            + [("GET", "/api/journals/meta")])
            resp, path = try_candidates(client, cands, verbose=args.verbose)
            print(f"Журнал изменений карточки #{card_id} (источник: {path})")
            rows = resp.as_list()
            if rows:
                _core.print_table(rows[:200])
                return 0
            return _print_result(resp, path)

        if action == "delete":
            if not args.yes and not ask_yesno(
                    f"Удалить карточку #{card_id}? Это действие необратимо!"):
                print("Отменено")
                return 0
            resp, path = try_candidates(
                client, equipment_detail_candidates(api_map, card_id, "DELETE"))
            return _print_result(resp, path)

    except _core.ApiError as e:
        print(f"[err] {e.message}")
        return 1
    raise SystemExit(f"Неизвестное действие: {action}")
