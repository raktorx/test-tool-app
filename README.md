# f2c_inventory — CLI для автоматизации inventory.f2c.ru

Скрипт для работы с личным кабинетом учёта оборудования F2C
(https://inventory.f2c.ru). Домен системы: **организации → адреса (здания) →
инвентаризации → карточки оборудования** — с серийными номерами, фото,
статусами «найдено/не найдено», переносами между адресами и осмотрами.

Основные возможности:

- **`addr`** — интерактивный поиск по адресам (организация → адрес, живой
  фильтр), просмотр карточки адреса и оборудования на нём;
- **`equipment`** — список и добавление оборудования: интерактивный мастер,
  JSON/CSV для скриптов, массовая загрузка;
- **`card`** — все операции с карточкой оборудования: просмотр,
  редактирование, перенос, найден/не найден, осмотр, фото, поиск по
  серийному номеру (вручную и по фото), журнал изменений, удаление;
- автоматическая авторизация с обновлением токена (`/api/auth/refresh`).

📖 Документация: [docs/GUIDE.md](docs/GUIDE.md) (общее руководство),
[docs/ADDRESSES.md](docs/ADDRESSES.md), [docs/EQUIPMENT.md](docs/EQUIPMENT.md),
[docs/CARD.md](docs/CARD.md). Команда `f2c_inventory.py docs` покажет пути.

Публичной документации у API системы нет, поэтому инструмент сам «вскрывает»
его: скачивает JS-бандлы фронтенда и извлекает из кода все эндпоинты
(метод + путь), либо анализирует HAR-лог реального трафика браузера.

## Установка

```bash
# Python 3.8+
pip install -r requirements.txt

# опционально, для обхода проверок TLS-отпечатка браузера:
pip install curl_cffi
```

## Быстрый старт

```bash
# 1. Составить карту API (скачает и разберёт JS-бандлы сайта)
python3 f2c_inventory.py recon

# 2. Войти (токен сохранится в ~/.config/f2c-inventory/session.json, chmod 600)
python3 f2c_inventory.py login
#    Рабочая почта: you@f2c.ru
#    Пароль: ********

# 3. Проверить сессию
python3 f2c_inventory.py status

# 4. Работать с данными
python3 f2c_inventory.py addr search "Ленина" --interactive
python3 f2c_inventory.py equipment add --interactive
python3 f2c_inventory.py card 500
python3 f2c_inventory.py list inventories --all --csv inventories.csv
```

Если `recon` не нашёл скриптов (например, SSR-приложение), эндпоинты можно
снять через DevTools (F12 → Network → XHR) и пользоваться командами `call`
и `list /api/путь` напрямую.

## Анализ реального трафика (HAR)

Самый точный способ восстановить схему API — проанализировать настоящий
сетевой лог браузера:

1. В Chrome: DevTools (F12) → вкладка **Network** → фильтр **Fetch/XHR** →
   пройдитесь по кабинету (вход, заявки, организации, карточки) →
   **Export HAR** (сохранится, например, `chrome-net-export-log.json`).
2. Запустите анализ:

```bash
# вариант 1: отдельный анализатор
python3 har2apimap.py chrome-net-export-log.json

# вариант 2: встроено в recon — карта сразу сохранится в api_map.json
python3 f2c_inventory.py recon --har chrome-net-export-log.json
```

Анализатор определяет автоматически:

- все вызовы API (метод + путь, числовые ID нормализуются в `{id}`);
- **эндпоинт входа**, имена полей (`email`, `password`), где в ответе токен;
- **формат заголовка авторизации** (`Authorization: Bearer {token}` и т.п.);
- эндпоинт «кто я» (для `status`);
- обёртку ответов (`success/error/data`), ключ списка, **пагинацию**.

После `recon --har` команда `login` идёт сразу по реальному эндпоинту входа и
использует правильный заголовок авторизации — без перебора. В печатаемом
отчёте пароли и токены маскируются (в сохраняемом `api_map.json` тоже).

## Команды

| Команда | Что делает |
|---|---|
| `recon` | скачивает страницу входа и JS-бандлы, извлекает эндпоинты API → `~/.config/f2c-inventory/api_map.json` |
| `login` / `logout` / `status` | вход (почта+пароль), выход, проверка сохранённой сессии |
| `health` | проверяет доступность API |
| `api-map [--filter sub]` | показывает карту эндпоинтов |
| `call METHOD PATH` | произвольный запрос: `-d '{"a":1}'`, `-d key=value`, `-d @file.json`, `-q key=value`, `--param id=42` |
| **`addr search\|list\|pick\|show`** | интерактивный поиск по адресам: поиск по тексту с живым фильтром, выбор организация → адрес, карточка адреса, оборудование на адресе |
| **`equipment list\|add\|show`** | оборудование: список по адресу/инвентаризации, добавление (мастер или `-d`), просмотр |
| **`card <id> [действие]`** | все операции с карточкой: show/edit/transfer/found/unfound/review/photos/photo-add/serial/serial-photo/history/delete; без действия — интерактивное меню |
| `list RESOURCE` | список записей: `--all`, `--limit`, `-q`, `--csv`, `--json`, `--count` |
| `get RESOURCE ID` | одна запись |
| `create RESOURCE -d '{...}'` | создать запись (`--dry-run` — без отправки) |
| `update RESOURCE ID -d '...'` | обновить запись |
| `delete RESOURCE ID [--yes]` | удалить (спросит подтверждение) |
| `export RESOURCE --csv/--json FILE` | полная выгрузка со всеми страницами (пагинация определяется автоматически) |
| `import RESOURCE --csv FILE [--mode create\|update\|patch --key id]` | массовое создание/обновление из CSV |
| `watch RESOURCE [--interval 60 --events f.jsonl]` | мониторинг: NEW / CHANGED / GONE; `--once` — одно сравнение |
| `run script.py` | выполнить свой процесс с готовым контекстом (см. ниже) |
| `docs` | список файлов документации |

`RESOURCE` — имя ресурса (`requests`, `organizations`, `warehouses`, …) или
полный путь `/api/...`. Имя автоматически сопоставляется с путём из карты API;
если совпадений нет, используется префикс `/api/v1`.

### Примеры

```bash
# Посмотреть первую страницу заявок
python3 f2c_inventory.py list requests

# Полная выгрузка в CSV (совместимо с Excel — utf-8-sig)
python3 f2c_inventory.py export requests --csv requests.csv

# Только новые заявки
python3 f2c_inventory.py list requests -q status=new --all --count

# Создать заявку
python3 f2c_inventory.py create requests -d '{"title":"Ноутбук HP","org_id":10}'

# Переместить карточку (шаблон пути из карты API)
python3 f2c_inventory.py call POST '/api/v1/cards/{cardId}/move' --param cardId=42 -d '{"warehouse_id":7}'

# Массовое обновление из CSV (колонка id — идентификатор)
python3 f2c_inventory.py import requests --csv changes.csv --mode update --key id --dry-run
python3 f2c_inventory.py import requests --csv changes.csv --mode update --key id

# Следить за новыми заявками (для cron / планировщика)
python3 f2c_inventory.py watch requests --once --events /var/log/requests.jsonl
```

## Собственные процессы (автоматизация)

Напишите `.py`-файл и запустите через `run` — внутри доступны:

- `api` — HTTP-клиент с сохранённой сессией (`api.get`, `api.post`, …,
  ответы с методами `.unwrap()` и `.as_list()`);
- `auth` — объект Auth (`auth.me()`, `auth.login()`);
- `api_map` — карта эндпоинтов из `recon`;
- `ApiError`, `print_table`, `now_iso`.

```bash
python3 f2c_inventory.py run examples/notify_new_requests.py
```

`examples/notify_new_requests.py` — готовый пример: находит новые заявки
(ресурс `requests`) и шлёт уведомление в Telegram (`TG_BOT_TOKEN`, `TG_CHAT_ID`).
Для запуска по расписанию:

```cron
*/10 * * * * cd /path/to/repo && F2C_EMAIL=you@f2c.ru F2C_PASSWORD=... \
  python3 f2c_inventory.py login && python3 f2c_inventory.py run examples/notify_new_requests.py
```

## Настройка

Переменные окружения: `F2C_BASE_URL`, `F2C_EMAIL`, `F2C_PASSWORD`, `F2C_TOKEN`,
`F2C_TIMEOUT`, `F2C_PROXY`, `F2C_CONFIG_DIR`.

Глобальные флаги: `--base-url`, `--timeout`, `--proxy`, `--min-interval`
(пауза между запросами — берегите сервер), `-v` (подробный лог), `--insecure`.

Данные хранятся в `~/.config/f2c-inventory/`:
`session.json` (токен, права 600), `api_map.json`, `bundles/`, `states/`.

## Как это устроено

1. **recon** запрашивает `/login`, находит `<script src=…>` и
   `modulepreload`, скачивает бандлы и ищет в них строковые литералы путей
   (`/api/...`), определяя HTTP-метод по контексту вызова
   (`axios.get(`, `fetch(`, `method: "POST"` и т.п.).
2. **login** перебирает эндпоинты-кандидаты (сначала из карты API, затем
   типовые `/api/v1/auth/login` и т.д.), отправляет `{email, password}` и
   рекурсивно ищет токен в любом поле ответа (`data.access_token` и т.п.).
   Перебор останавливается на первом существующем маршруте — лишних попыток
   входа (и риска блокировки) нет.
3. Ответы сервера разворачиваются из обёртки `{"success", "error", "data"}`;
   пагинация (`page`/`limit`) и ключ списка (`data.items` и т.п.) определяются
   автоматически.

## Если что-то не работает

- **Сетевая ошибка / TLS EOF** — сервер может фильтровать не-браузерные
  запросы. Установите `curl_cffi` (клиент выберет его автоматически для
  https) или используйте `--proxy`, если работаете из-под корпоративного
  прокси/VPN.
- **`Модуль 'requests' не установлен`** — `pip install -r requirements.txt`.
- **401 UNAUTHORIZED** — токен протух: повторите `login`.
- **Не находит ресурс** — проверьте карту: `api-map`, и укажите полный путь
  `/api/...` вручную.

## Ограничения и этика

Это внутренняя рабочая система. Используйте инструмент в рамках своих
должностных задач: не перегружайте сервер (`--min-interval`), не выгружайте
чужие персональные данные без необходимости и не храните пароль в открытых
файлах (лучше `F2C_PASSWORD` в окружении или интерактивный ввод).
