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
