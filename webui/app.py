#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
webui/app.py — графический интерфейс (GUI) для inventory.f2c.ru.

Тонкая веб-оболочка над доменным ядром (f2c_ops.py / f2c_inventory.py),
реализующая архитектуру из docs/GUI_ARCHITECTURE.md:

  * иерархия Организация → Адрес → Инвентаризация → Оборудование;
  * авторизация с автообновлением токена (401 → /api/auth/refresh → повтор);
  * recon-движок: карта api_map.json (+ встроенный пример из реального бандла);
  * candidate trial: перебор эндпоинтов через try_candidates();
  * ленивая загрузка списков (организация → адреса), dry-run предпросмотр.

Запуск (демо с мок-бэкендом):

    python3 webui/mock_api.py &                       # имитация API F2C
    F2C_BASE_URL=http://127.0.0.1:8799 \
    F2C_CONFIG_DIR=/tmp/f2c-gui-config \
    uvicorn webui.app:app --host 0.0.0.0 --port 8000

Демо-вход: любой email, пароль "secret".
Без мок-бэкенда укажите реальный F2C_BASE_URL=https://inventory.f2c.ru.
"""

import json
import os
import re
import sys
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import f2c_inventory as core
import f2c_ops as ops
from webui import docs_store

BASE_URL = os.environ.get("F2C_BASE_URL", "https://inventory.f2c.ru").rstrip("/")
DEFAULT_INVENTORY_ID = os.environ.get("F2C_DEFAULT_INVENTORY_ID", "").strip()
CFG_DIR = Path(os.environ.get(
    "F2C_CONFIG_DIR", str(Path.home() / ".config" / "f2c-inventory")))
SAMPLE_MAP = ROOT / "webui" / "sample_api_map.json"
DEMO = "127.0.0.1" in BASE_URL or "localhost" in BASE_URL

app = FastAPI(title="F2C Инвентаризация", docs_url="/api/docs",
              redoc_url="/api/redoc", openapi_url="/api/openapi.json")
app.mount("/static", StaticFiles(directory=str(ROOT / "webui" / "static")),
          name="static")
templates = Jinja2Templates(directory=str(ROOT / "webui" / "templates"))

_client = None
_auth = None
_api_map = {}


def ensure_setup():
    """Ленивая инициализация ядра (клиент, сессия, карта API)."""
    global _client, _auth, _api_map
    if _client is None:
        CFG_DIR.mkdir(parents=True, exist_ok=True)
        map_file = CFG_DIR / "api_map.json"
        if not map_file.exists() and SAMPLE_MAP.exists():
            map_file.write_text(SAMPLE_MAP.read_text("utf-8"))
        _api_map = core.load_api_map(CFG_DIR)
        _client = core.Client(base_url=BASE_URL, timeout=15)
        _auth = core.Auth(_client, CFG_DIR)
        _auth.load()
        _client.on_401 = _safe_refresh
    return _client, _auth, _api_map


def _safe_refresh():
    try:
        return bool(_auth.refresh(_api_map))
    except Exception:
        return False


# --------------------------------------------------------------------------
# Помощники рендера
# --------------------------------------------------------------------------

def flash(request: Request):
    f = request.query_params.get("flash", "")
    if ":" in f:
        kind, _, msg = f.partition(":")
        return (kind, msg)
    return ("ok", f) if f else None


def render(request, name, **ctx):
    client, auth, api_map = ensure_setup()
    return templates.TemplateResponse(
        request=request, name=name,
        context={"user_email": auth.session.get("email"),
                 "flash": flash(request), "demo": DEMO,
                 "default_inventory_id": DEFAULT_INVENTORY_ID, **ctx})


def redirect(path, msg="", kind="ok"):
    if msg:
        path += ("&" if "?" in path else "?") + "flash=" + urllib.parse.quote(
            f"{kind}:{msg}")
    return RedirectResponse(path, status_code=303)


def err(request, e, back="/"):
    return render(request, "error.html", error=f"{e.code}: {e.message}",
                  back=back)


def logged(request):
    client, auth, api_map = ensure_setup()
    return request.cookies.get("f2c_ui") == "1" and bool(client.token)


def api_error_response(request, e, back):
    return err(request, e, back)


def start_path():
    """Initial workspace, optionally scoped to a configured inventory."""
    if DEFAULT_INVENTORY_ID:
        return "/equipment?inventory_id=" + urllib.parse.quote(DEFAULT_INVENTORY_ID)
    return "/dashboard"


# --------------------------------------------------------------------------
# Авторизация
# --------------------------------------------------------------------------

@app.get("/manifest.webmanifest", include_in_schema=False)
def manifest():
    return FileResponse(ROOT / "webui" / "static" / "manifest.webmanifest",
                        media_type="application/manifest+json")


@app.get("/sw.js", include_in_schema=False)
def service_worker():
    response = FileResponse(ROOT / "webui" / "static" / "sw.js",
                            media_type="application/javascript")
    response.headers["Service-Worker-Allowed"] = "/"
    response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return redirect(start_path())


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    """Operational overview assembled through the same domain layer as CLI."""
    if not logged(request):
        return redirect("/login")
    client, auth, api_map = ensure_setup()
    try:
        orgs = ops.list_organizations(client, api_map)
        addresses = ops.collect_addresses(client, api_map)
        equipment = ops.equipment_rows(client, api_map, all_pages=True)
        inv_resp, _ = ops.try_candidates(
            client, ops._dedupe(ops._map_candidates(
                api_map, re.compile(r"/inventories$", re.I), "GET")
                + [("GET", "/api/inventories")]))
        inventories = inv_resp.as_list()
    except core.ApiError as e:
        return err(request, e, "/dashboard")

    statuses = {}
    for item in equipment:
        status = str(item.get("status") or "unknown").lower()
        statuses[status] = statuses.get(status, 0) + 1
    active_inventories = sum(
        1 for item in inventories if str(item.get("status", "")).lower() == "active")
    # Most APIs return the registry in creation order. Avoid assuming that IDs
    # are numeric (some installations use UUIDs).
    recent = list(reversed(equipment[-5:]))
    return render(request, "dashboard.html", org_count=len(orgs),
                  address_count=len(addresses), equipment_count=len(equipment),
                  active_inventories=active_inventories, statuses=statuses,
                  recent=recent)


# --------------------------------------------------------------------------
# Встроенная база знаний
# --------------------------------------------------------------------------

def docs_db_path():
    return CFG_DIR / "docs.db"


@app.get("/docs", response_class=HTMLResponse)
def documentation(request: Request, q: str = "", category: str = ""):
    if not logged(request):
        return redirect("/login")
    articles = docs_store.list_articles(docs_db_path(), q.strip(), category)
    return render(request, "docs.html", articles=articles,
                  categories=docs_store.categories(docs_db_path()),
                  query=q.strip(), selected_category=category)


@app.get("/docs/{slug}", response_class=HTMLResponse)
def documentation_article(request: Request, slug: str):
    if not logged(request):
        return redirect("/login")
    article = docs_store.get_article(docs_db_path(), slug)
    if not article:
        return redirect("/docs", "Статья не найдена", "err")
    return render(request, "docs_article.html", article=article)


@app.get("/api/ui/docs/search")
def documentation_search(request: Request, q: str = ""):
    if not logged(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return JSONResponse(docs_store.list_articles(docs_db_path(), q.strip()))


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if logged(request):
        return redirect(start_path())
    return render(request, "login.html", error="")


@app.post("/login", response_class=HTMLResponse)
def login_post(request: Request, email: str = Form(...),
               password: str = Form(...)):
    client, auth, api_map = ensure_setup()
    try:
        auth.login(email.strip(), password, api_map)
    except SystemExit as e:
        return render(request, "login.html", error=str(e))
    target = start_path()
    target += ("&" if "?" in target else "?") + "flash=" + urllib.parse.quote(
        "ok:Вход выполнен")
    resp = RedirectResponse(target, status_code=303)
    resp.set_cookie("f2c_ui", "1", httponly=True, samesite="lax")
    return resp


@app.get("/logout")
def logout():
    client, auth, api_map = ensure_setup()
    auth.clear()
    resp = RedirectResponse("/login?flash=" + urllib.parse.quote(
        "ok:Сессия завершена"), status_code=303)
    resp.delete_cookie("f2c_ui")
    return resp


# --------------------------------------------------------------------------
# Организации и адреса
# --------------------------------------------------------------------------

@app.get("/organizations", response_class=HTMLResponse)
def organizations(request: Request):
    if not logged(request):
        return redirect("/login")
    client, auth, api_map = ensure_setup()
    try:
        orgs = ops.list_organizations(client, api_map)
    except core.ApiError as e:
        return err(request, e, "/organizations")
    return render(request, "organizations.html", orgs=orgs)


@app.get("/organizations/{org_id}/addresses", response_class=HTMLResponse)
def org_addresses_page(request: Request, org_id: int):
    if not logged(request):
        return redirect("/login")
    client, auth, api_map = ensure_setup()
    try:
        orgs = ops.list_organizations(client, api_map)
        org = next((o for o in orgs if str(o.get("id")) == str(org_id)), None)
        addrs = ops.org_addresses(client, api_map, org_id)
    except core.ApiError as e:
        return err(request, e, "/organizations")
    rows = []
    for a in addrs:
        if isinstance(a, dict):
            rows.append({"id": a.get("id"), "label": ops.addr_display(a)})
    return render(request, "org_addresses.html", org=org, org_id=org_id,
                  addresses=rows)


@app.get("/addresses", response_class=HTMLResponse)
def addresses(request: Request, q: str = ""):
    if not logged(request):
        return redirect("/login")
    client, auth, api_map = ensure_setup()
    try:
        addrs = ops.collect_addresses(client, api_map)
    except core.ApiError as e:
        return err(request, e, "/addresses")
    scored = sorted(((ops.score_record(a, q), a) for a in addrs),
                    key=lambda t: (-t[0], ops.addr_display(t[1]).lower()))
    if q:
        scored = [(s, a) for s, a in scored if s > 0]
    rows = [{"id": a.get("id"), "label": ops.addr_display(a),
             "org": (a.get("_org_name") or "") if isinstance(a, dict) else ""}
            for _, a in scored]
    return render(request, "addresses.html", addresses=rows, query=q)


@app.get("/inventories", response_class=HTMLResponse)
def inventories(request: Request):
    if not logged(request):
        return redirect("/login")
    client, auth, api_map = ensure_setup()
    try:
        resp, path = ops.try_candidates(
            client, ops._dedupe(ops._map_candidates(
                api_map, re.compile(r"/inventories$", re.I), "GET")
                + [("GET", "/api/inventories")]))
        rows = resp.as_list()
    except core.ApiError as e:
        return err(request, e, "/inventories")
    return render(request, "inventories.html", inventories=rows)


# --------------------------------------------------------------------------
# Оборудование
# --------------------------------------------------------------------------

def equipment_page(request, address_id="", inventory_id="", q=""):
    client, auth, api_map = ensure_setup()
    params = {}
    if address_id:
        params["address_id"] = address_id
    if inventory_id:
        params["inventory_id"] = inventory_id
    try:
        rows = ops.equipment_rows(client, api_map, params=params,
                                  all_pages=True)
    except core.ApiError as e:
        return err(request, e, "/equipment")
    if q:
        rows = [r for r in rows if q.lower() in json.dumps(
            r, ensure_ascii=False).lower()]
    return render(request, "equipment.html", rows=rows,
                  address_id=address_id, inventory_id=inventory_id, q=q)


@app.get("/equipment", response_class=HTMLResponse)
def equipment(request: Request, address_id: str = "",
              inventory_id: str = "", q: str = ""):
    if not logged(request):
        return redirect("/login")
    return equipment_page(request, address_id, inventory_id, q)


@app.get("/equipment/new", response_class=HTMLResponse)
def equipment_new_page(request: Request):
    if not logged(request):
        return redirect("/login")
    client, auth, api_map = ensure_setup()
    try:
        orgs = ops.list_organizations(client, api_map)
    except core.ApiError as e:
        return err(request, e, "/equipment")
    return render(request, "equipment_new.html", orgs=orgs)


@app.post("/equipment/new", response_class=HTMLResponse)
def equipment_new_post(request: Request, name: str = Form(...),
                       address_id: str = Form(""),
                       serial_number: str = Form(""),
                       inventory_number: str = Form(""),
                       quantity: str = Form(""), comment: str = Form(""),
                       dry_run: str = Form("")):
    if not logged(request):
        return redirect("/login")
    client, auth, api_map = ensure_setup()
    payload = {"name": name.strip()}
    if address_id:
        payload["address_id"] = core.smart_value(address_id)
    if serial_number:
        payload["serial_number"] = serial_number.strip()
    if inventory_number:
        payload["inventory_number"] = inventory_number.strip()
    if quantity:
        payload["quantity"] = core.smart_value(quantity)
    if comment:
        payload["comment"] = comment.strip()
    if dry_run:
        return render(request, "preview.html",
                      method="POST",
                      paths=["/api/equipment (из карты: /equipment)"],
                      payload=json.dumps(payload, ensure_ascii=False, indent=2),
                      back="/equipment/new")
    cands = ops._dedupe(ops._map_candidates(
        api_map, ops.EQUIP_LIST_PAT, "POST", prefer_post=True)
        + [("POST", "/api/equipment"), ("POST", "/api/v1/equipment")])
    try:
        resp, path = ops.try_candidates(client, cands, json_body=payload)
        data = resp.unwrap()
    except core.ApiError as e:
        return err(request, e, "/equipment/new")
    new_id = data.get("id") if isinstance(data, dict) else None
    return redirect(f"/equipment/{new_id}" if new_id else "/equipment",
                    "Оборудование добавлено")


# --------------------------------------------------------------------------
# Карточка оборудования
# --------------------------------------------------------------------------

CARD_TABS = [
    ("/equipment/{id}", "Обзор"),
    ("/equipment/{id}/actions", "Действия"),
    ("/equipment/{id}/photos", "Фото"),
    ("/equipment/{id}/serial", "Серийник"),
    ("/equipment/{id}/history", "История"),
]


def card_context(request, card_id):
    client, auth, api_map = ensure_setup()
    try:
        resp, path = ops.try_candidates(
            client, ops.equipment_detail_candidates(api_map, card_id))
        card = resp.unwrap()
    except core.ApiError as e:
        return None, e, None, None, None
    return client, None, api_map, card, path


@app.get("/equipment/{card_id}", response_class=HTMLResponse)
def card_view(request: Request, card_id: int):
    if not logged(request):
        return redirect("/login")
    client, e, api_map, card, path = card_context(request, card_id)
    if e:
        return err(request, e, "/equipment")
    return render(request, "card.html", card=card, card_id=card_id,
                  tab="overview", source=path)


@app.get("/equipment/{card_id}/actions", response_class=HTMLResponse)
def card_actions_page(request: Request, card_id: int):
    if not logged(request):
        return redirect("/login")
    client, e, api_map, card, path = card_context(request, card_id)
    if e:
        return err(request, e, "/equipment")
    try:
        inv_resp, _ = ops.try_candidates(
            client, ops._dedupe(ops._map_candidates(
                api_map, re.compile(r"/inventories$", re.I), "GET")
                + [("GET", "/api/inventories")]))
        inventories = inv_resp.as_list()
        addrs = ops.collect_addresses(client, api_map)
    except core.ApiError as e2:
        return err(request, e2, f"/equipment/{card_id}")
    addr_options = [{"id": a.get("id"), "label": ops.addr_display(a)}
                    for a in addrs if isinstance(a, dict)]
    return render(request, "card_actions.html", card=card, card_id=card_id,
                  tab="actions", inventories=inventories,
                  addresses=addr_options)


@app.post("/equipment/{card_id}/action/{action}", response_class=HTMLResponse)
def card_action_post(request: Request, card_id: int, action: str,
                     inventory_id: str = Form(""), to_address_id: str = Form(""),
                     comment: str = Form(""), dry_run: str = Form("")):
    if not logged(request):
        return redirect("/login")
    client, auth, api_map = ensure_setup()
    back = f"/equipment/{card_id}/actions"
    if action not in ("transfer", "found", "unfound", "review"):
        return redirect(back, "Неизвестное действие", "err")

    payload = None
    if action == "transfer":
        if not to_address_id:
            return redirect(back, "Выберите адрес-получатель", "err")
        payload = {"address_id": core.smart_value(to_address_id)}
    elif action == "review" and comment:
        payload = {"comment": comment}

    inv_id = core.smart_value(inventory_id) if inventory_id else None
    if dry_run:
        patterns = [ep["path"] for ep in api_map.get("endpoints", [])
                    if action in ep.get("path", "")]
        patterns = patterns[:3] + (["/api/equipment/{id}/" + action]
                                   if not patterns else [])
        return render(request, "preview.html",
                      method=ops.ACTION_METHODS.get(action, "POST"),
                      paths=patterns,
                      payload=json.dumps(payload or {}, ensure_ascii=False,
                                         indent=2),
                      back=back)
    try:
        resp, path = ops.card_action(client, api_map, action, card_id,
                                     payload=payload, inventory_id=inv_id)
        resp.unwrap()
    except core.ApiError as e:
        return err(request, e, back)
    return redirect(back, f"Действие «{action}» выполнено (через {path})")


@app.get("/equipment/{card_id}/photos", response_class=HTMLResponse)
def card_photos_page(request: Request, card_id: int):
    if not logged(request):
        return redirect("/login")
    client, e, api_map, card, path = card_context(request, card_id)
    if e:
        return err(request, e, "/equipment")
    try:
        resp, ppath = ops._photo_endpoint(client, api_map, card_id, "GET",
                                          None, None)
        photos = resp.as_list()
    except core.ApiError as e2:
        return err(request, e2, f"/equipment/{card_id}")
    return render(request, "card_photos.html", card=card, card_id=card_id,
                  tab="photos", photos=photos)


@app.post("/equipment/{card_id}/photos", response_class=HTMLResponse)
async def card_photos_upload(request: Request, card_id: int,
                             file: UploadFile = File(...),
                             dry_run: str = Form("")):
    if not logged(request):
        return redirect("/login")
    client, auth, api_map = ensure_setup()
    back = f"/equipment/{card_id}/photos"
    data = await file.read()
    if dry_run:
        return render(request, "preview.html", method="POST",
                      paths=["/api/equipment/{id}/photos (multipart)"],
                      payload=f"файл: {file.filename} ({len(data)} байт)",
                      back=back)
    files = {"photo": (file.filename or "photo.jpg", data,
                       file.content_type or "application/octet-stream")}
    try:
        resp, path = ops._photo_endpoint(client, api_map, card_id, "POST",
                                         None, None, files=files)
        resp.unwrap()
    except core.ApiError as e:
        return err(request, e, back)
    return redirect(back, "Фото загружено")


@app.get("/equipment/{card_id}/serial", response_class=HTMLResponse)
def card_serial_page(request: Request, card_id: int):
    if not logged(request):
        return redirect("/login")
    client, e, api_map, card, path = card_context(request, card_id)
    if e:
        return err(request, e, "/equipment")
    return render(request, "card_serial.html", card=card, card_id=card_id,
                  tab="serial")


@app.post("/equipment/{card_id}/serial", response_class=HTMLResponse)
def card_serial_post(request: Request, card_id: int, number: str = Form(...),
                     dry_run: str = Form("")):
    if not logged(request):
        return redirect("/login")
    client, auth, api_map = ensure_setup()
    back = f"/equipment/{card_id}/serial"
    payload = {"serial": number.strip()}
    if dry_run:
        return render(request, "preview.html", method="POST",
                      paths=["/api/equipment/serial-lookup/manual"],
                      payload=json.dumps(payload, ensure_ascii=False, indent=2),
                      back=back)
    cands = ops._dedupe(ops._map_candidates(
        api_map, ops.SERIAL_MANUAL_PAT, "POST", prefer_post=True)
        + [("POST", "/api/equipment/serial-lookup/manual")])
    try:
        resp, path = ops.try_candidates(client, cands, json_body=payload)
        found = resp.unwrap()
    except core.ApiError as e:
        return err(request, e, back)
    return render(request, "card_serial.html", card=card_context(
        request, card_id)[3], card_id=card_id, tab="serial",
        result={"path": path, "data": json.dumps(found, ensure_ascii=False,
                                                 indent=2)})


@app.post("/equipment/{card_id}/serial-photo", response_class=HTMLResponse)
async def card_serial_photo(request: Request, card_id: int,
                            file: UploadFile = File(...),
                            dry_run: str = Form("")):
    if not logged(request):
        return redirect("/login")
    client, auth, api_map = ensure_setup()
    back = f"/equipment/{card_id}/serial"
    data = await file.read()
    if dry_run:
        return render(request, "preview.html", method="POST",
                      paths=["/api/equipment/serial-lookup/photo"],
                      payload=f"файл: {file.filename} ({len(data)} байт)",
                      back=back)
    files = {"photo": (file.filename or "tag.jpg", data,
                       file.content_type or "application/octet-stream")}
    cands = ops._dedupe(ops._map_candidates(
        api_map, ops.SERIAL_PHOTO_PAT, "POST", prefer_post=True)
        + [("POST", "/api/equipment/serial-lookup/photo")])
    try:
        resp, path = ops.try_candidates(client, cands, files=files)
        found = resp.unwrap()
    except core.ApiError as e:
        return err(request, e, back)
    return render(request, "card_serial.html", card=card_context(
        request, card_id)[3], card_id=card_id, tab="serial",
        result={"path": path, "data": json.dumps(found, ensure_ascii=False,
                                                 indent=2)})


@app.get("/equipment/{card_id}/history", response_class=HTMLResponse)
def card_history_page(request: Request, card_id: int):
    if not logged(request):
        return redirect("/login")
    client, e, api_map, card, path = card_context(request, card_id)
    if e:
        return err(request, e, "/equipment")
    cands = ops._dedupe(
        [("GET", f"/api/equipment/{card_id}/history")]
        + ops._map_candidates(api_map, ops.HISTORY_PAT, "GET")
        + [("GET", "/api/journals/meta")])
    try:
        resp, hpath = ops.try_candidates(client, cands)
        history = resp.as_list()
    except core.ApiError as e2:
        return err(request, e2, f"/equipment/{card_id}")
    return render(request, "card_history.html", card=card, card_id=card_id,
                  tab="history", history=history, source=hpath)


@app.post("/equipment/{card_id}/edit", response_class=HTMLResponse)
def card_edit_post(request: Request, card_id: int, name: str = Form(...),
                   serial_number: str = Form(""), inventory_number: str = Form(""),
                   status: str = Form(""), comment: str = Form(""),
                   dry_run: str = Form("")):
    if not logged(request):
        return redirect("/login")
    client, auth, api_map = ensure_setup()
    back = f"/equipment/{card_id}"
    payload = {"name": name.strip()}
    if serial_number:
        payload["serial_number"] = serial_number.strip()
    if inventory_number:
        payload["inventory_number"] = inventory_number.strip()
    if status:
        payload["status"] = status.strip()
    if comment:
        payload["comment"] = comment.strip()
    if dry_run:
        return render(request, "preview.html", method="PUT",
                      paths=["/api/equipment/{id} (PUT, фолбэк PATCH)"],
                      payload=json.dumps(payload, ensure_ascii=False, indent=2),
                      back=back)
    cands = ops._dedupe(
        ops.equipment_detail_candidates(api_map, card_id, "PUT")
        + ops.equipment_detail_candidates(api_map, card_id, "PATCH"))
    try:
        resp, path = ops.try_candidates(client, cands, json_body=payload)
        resp.unwrap()
    except core.ApiError as e:
        return err(request, e, back)
    return redirect(back, "Карточка обновлена")


@app.post("/equipment/{card_id}/delete", response_class=HTMLResponse)
def card_delete_post(request: Request, card_id: int,
                     confirm: str = Form(""), dry_run: str = Form("")):
    if not logged(request):
        return redirect("/login")
    client, auth, api_map = ensure_setup()
    if confirm != "DELETE":
        return redirect(f"/equipment/{card_id}", "Подтвердите удаление", "err")
    if dry_run:
        return render(request, "preview.html", method="DELETE",
                      paths=[f"/api/equipment/{card_id}"],
                      payload="(без тела)", back=f"/equipment/{card_id}")
    try:
        resp, path = ops.try_candidates(
            client, ops.equipment_detail_candidates(api_map, card_id, "DELETE"))
        resp.unwrap()
    except core.ApiError as e:
        return err(request, e, f"/equipment/{card_id}")
    return redirect("/equipment", "Карточка удалена")


# --------------------------------------------------------------------------
# JSON-эндпоинты для ленивой загрузки форм
# --------------------------------------------------------------------------

@app.get("/api/ui/addresses")
def ui_addresses(request: Request, org_id: int):
    if not logged(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    client, auth, api_map = ensure_setup()
    try:
        addrs = ops.org_addresses(client, api_map, org_id)
    except core.ApiError as e:
        return JSONResponse({"error": e.message}, status_code=400)
    return JSONResponse([{"id": a.get("id"), "label": ops.addr_display(a)}
                         for a in addrs if isinstance(a, dict)])


@app.get("/api/ui/inventories")
def ui_inventories():
    client, auth, api_map = ensure_setup()
    try:
        resp, _ = ops.try_candidates(
            client, ops._dedupe(ops._map_candidates(
                api_map, re.compile(r"/inventories$", re.I), "GET")
                + [("GET", "/api/inventories")]))
        rows = resp.as_list()
    except core.ApiError as e:
        return JSONResponse({"error": e.message}, status_code=400)
    return JSONResponse([{"id": r.get("id"),
                          "label": str(r.get("title") or r.get("id"))}
                         for r in rows if isinstance(r, dict)])
