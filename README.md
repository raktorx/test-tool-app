# test-tool-app

Набор CLI-инструментов для автоматизации рабочих задач.

## Инструменты

### 1. f2c_inventory — автоматизация inventory.f2c.ru (Python)

CLI для личного кабинета учёта оборудования F2C
(https://inventory.f2c.ru): домен «организации → адреса (здания) →
инвентаризации → карточки оборудования».

- **`addr`** — интерактивный поиск по адресам: живой фильтр, выбор
  «организация → адрес», карточка адреса и оборудование на нём;
- **`equipment`** — список и добавление оборудования (интерактивный мастер,
  JSON/CSV для скриптов, массовая загрузка);
- **`card <id>`** — все операции с карточкой оборудования: просмотр,
  редактирование, перенос, «найдено/не найдено», осмотр, фото, поиск по
  серийному номеру (вручную и по фото), журнал изменений, удаление;
- выгрузки CSV/JSON, массовый импорт, мониторинг изменений, авторизация
  с автообновлением токена (`/api/auth/refresh`).

**Быстрый старт:**

```bash
pip install -r requirements.txt
python3 f2c_inventory.py login
python3 f2c_inventory.py addr search "Ленина" --interactive
python3 f2c_inventory.py equipment add --interactive
python3 f2c_inventory.py card 500
```

Документация: [docs/GUIDE.md](docs/GUIDE.md) — общее руководство,
[docs/ADDRESSES.md](docs/ADDRESSES.md), [docs/EQUIPMENT.md](docs/EQUIPMENT.md),
[docs/CARD.md](docs/CARD.md),
[docs/GUI_ARCHITECTURE.md](docs/GUI_ARCHITECTURE.md) — архитектура и логика
графического интерфейса.

### Веб-интерфейс (webui/)

Графический интерфейс по архитектуре `docs/GUI_ARCHITECTURE.md`: тонкая
веб-оболочка (FastAPI + Jinja2) над тем же доменным ядром
(`f2c_ops.py`/`f2c_inventory.py`). Экраны: организации → адреса (живой
поиск) → реестр оборудования → карточка с вкладками (обзор/действия/фото/
серийник/история), мастер добавления с ленивой загрузкой адресов и
dry-run предпросмотром.

```bash
pip install -r requirements.txt fastapi "uvicorn[standard]" jinja2 python-multipart

# демо: мок-бэкенд, имитирующий схему F2C
python3 webui/mock_api.py &                          # http://127.0.0.1:8799
F2C_BASE_URL=http://127.0.0.1:8799 \
F2C_CONFIG_DIR=/tmp/f2c-gui-config \
python3 -m uvicorn webui.app:app --host 0.0.0.0 --port 8000

# или против реального кабинета (с рабочего компьютера):
F2C_BASE_URL=https://inventory.f2c.ru python3 -m uvicorn webui.app:app --port 8000
```

Демо-вход: любой email, пароль `secret`.

### 2. analyzer.sh — мобильный аудит веб-ресурсов (Termux)

Интерактивный Bash-скрипт для быстрого технического аудита веб-страниц с
Android-устройства (Termux): метрики производительности (DNS, TCP, TLS,
TTFB), анализ HTTP-заголовков, JSON-отчёт, загрузка HTML-копии страницы в
GitHub Gist. GUI-режим через `termux-dialog` и CLI/headless-режим для
автоматизации.

**Быстрый старт:**

```bash
bash analyzer.sh https://example.com
```

Полная документация: [docs/ANALYZER.md](docs/ANALYZER.md), тесты —
`tests/run_tests.sh`, история изменений — `CHANGELOG.md`.

## Структура репозитория

| Путь | Описание |
|---|---|
| `f2c_inventory.py` | основной CLI инвентаризации F2C |
| `f2c_ops.py` | доменные операции (адреса, оборудование, карточки) |
| `interactive.py` | интерактивные меню CLI (стрелки, живой фильтр) |
| `har2apimap.py` | анализатор HAR-логов для карты API |
| `examples/`, `docs/` | примеры процессов и документация F2C-инструмента |
| `analyzer.sh` | аудитор веб-ресурсов для Termux |
| `tests/` | тесты анализатора |
| `requirements.txt` | зависимости Python-инструмента |
