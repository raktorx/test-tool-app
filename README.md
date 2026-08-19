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

Графический интерфейс по архитектуре `docs/GUI_ARCHITECTURE.md`: полноценное
адаптивное PWA (FastAPI + Jinja2 + Service Worker) над тем же доменным ядром
(`f2c_ops.py`/`f2c_inventory.py`). Главный дашборд показывает оперативные
метрики и состояние парка. Экраны: организации → адреса (живой поиск) →
реестр оборудования → карточка с вкладками (обзор/действия/фото/серийник/
история), мастер добавления с ленивой загрузкой адресов и dry-run
предпросмотром. Приложение устанавливается на рабочий стол, поддерживает
светлую/тёмную тему и информативный офлайн-экран.

```bash
pip install -r requirements.txt

# демо: мок-бэкенд, имитирующий схему F2C
python3 webui/mock_api.py &                          # http://127.0.0.1:8799
F2C_BASE_URL=http://127.0.0.1:8799 \
F2C_CONFIG_DIR=/tmp/f2c-gui-config \
python3 -m uvicorn webui.app:app --host 0.0.0.0 --port 8000

# или против реального кабинета (с рабочего компьютера):
F2C_BASE_URL=https://inventory.f2c.ru python3 -m uvicorn webui.app:app --port 8000
```

Демо-вход: любой email, пароль `secret`.

#### Быстрый запуск с нуля

```bash
# 1. Изолированное окружение и зависимости
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Демо-API (терминал №1)
python3 webui/mock_api.py

# 3. GUI (терминал №2)
F2C_BASE_URL=http://127.0.0.1:8799 \
F2C_CONFIG_DIR=/tmp/f2c-gui-config \
uvicorn webui.app:app --host 0.0.0.0 --port 8000
```

Откройте <http://localhost:8000>. Для рабочего сервера используйте
`F2C_BASE_URL=https://inventory.f2c.ru`; токены не нужно передавать через
переменные окружения — вход выполняется в GUI, сессия сохраняется с правами
`0600`.

Для запуска сразу в контексте конкретной инвентаризации передайте её UUID
отдельно от базового URL:

```bash
F2C_BASE_URL=https://inventory.f2c.ru \
F2C_DEFAULT_INVENTORY_ID=69705c06-d8c4-475b-bd20-354c79b02c55 \
uvicorn webui.app:app --host 0.0.0.0 --port 8000
```

После входа GUI автоматически откроет отфильтрованный реестр этой
инвентаризации. Полный кабинетный URL нельзя использовать как `F2C_BASE_URL`:
`/cabinet/inventories/...` — маршрут браузерного интерфейса, тогда как ядру
нужен origin `https://inventory.f2c.ru` для запросов `/api/...`.

После входа раздел **«Документация»** содержит полноценную встроенную базу
знаний: быстрый старт, руководство по всем экранам, рабочие процессы с
оборудованием, безопасность API, диагностику и SQLite-каталог готовых Bash/
Python-примеров. База создаётся в `F2C_CONFIG_DIR/docs.db` и доступна через
поиск в интерфейсе; исходные проверенные материалы находятся в
`webui/docs_store.py`.

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
