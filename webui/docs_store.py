"""SQLite-backed knowledge base used by the F2C Hub documentation UI."""

import json
import sqlite3
from pathlib import Path

SCHEMA_VERSION = "1"

ARTICLES = [
    {
        "slug": "quick-start",
        "title": "Быстрый запуск F2C Hub",
        "summary": "Установка, демо-режим и подключение к рабочему кабинету за несколько минут.",
        "category": "Начало работы",
        "icon": "▶",
        "order": 10,
        "sections": [
            {"title": "1. Подготовка", "text": "Требуется Python 3.10 или новее. Выполните команды из корня репозитория. Виртуальное окружение изолирует зависимости приложения.", "code": "python3 -m venv .venv\nsource .venv/bin/activate\npip install -r requirements.txt", "language": "bash"},
            {"title": "2. Запуск демо", "text": "Мок-сервер содержит организации, адреса, инвентаризации и карточки оборудования. Он подходит для безопасного знакомства со всеми функциями.", "code": "python3 webui/mock_api.py &\nF2C_BASE_URL=http://127.0.0.1:8799 \\\nF2C_CONFIG_DIR=/tmp/f2c-gui-config \\\nuvicorn webui.app:app --host 0.0.0.0 --port 8000", "language": "bash", "note": "Откройте http://localhost:8000. Укажите любую почту и пароль secret."},
            {"title": "3. Подключение к F2C", "text": "Для рабочего режима замените адрес мок-сервера на inventory.f2c.ru. Сессия и карта API хранятся в ~/.config/f2c-inventory с закрытыми правами.", "code": "F2C_BASE_URL=https://inventory.f2c.ru \\\nuvicorn webui.app:app --host 0.0.0.0 --port 8000", "language": "bash", "warning": "Не публикуйте session.json и не передавайте токены в командной строке или чатах."},
            {"title": "4. Проверка", "text": "После входа откройте «Обзор». Счётчики должны показать организации, адреса и оборудование. Затем откройте любую карточку из реестра."}
        ]
    },
    {
        "slug": "interface-guide",
        "title": "Руководство по интерфейсу",
        "summary": "Навигация, поиск, реестр, карточки и установка PWA.",
        "category": "Руководство",
        "icon": "◇",
        "order": 20,
        "sections": [
            {"title": "Обзор", "text": "Главный экран показывает объём парка, число организаций и адресов, активные инвентаризации, последние карточки и распределение оборудования по статусам."},
            {"title": "Организации и адреса", "text": "Структура данных идёт от организации к адресу. В разделе «Организации» выберите учреждение, затем площадку. Общий раздел «Адреса» выполняет живой поиск по городу, улице, дому и названию организации."},
            {"title": "Реестр оборудования", "text": "Фильтруйте реестр по адресу, инвентаризации или свободному тексту. Нажатие на строку открывает карточку. Статус цветом показывает эксплуатацию, ремонт, найденный или отсутствующий объект."},
            {"title": "Карточка объекта", "text": "Вкладка «Обзор» содержит реквизиты и редактирование. «Действия» выполняет перенос, отметки и осмотр. «Фото» хранит изображения, «Серийник» запускает поиск и OCR, «История» показывает журнал изменений."},
            {"title": "Dry-run", "text": "Включите Dry-run перед изменением данных, чтобы увидеть HTTP-метод, маршруты-кандидаты и JSON без отправки запроса. Это рекомендуемый режим для первой проверки интеграции."},
            {"title": "Установка PWA", "text": "Нажмите «Установить приложение» в нижней части меню или используйте установку сайта в браузере. F2C Hub откроется как отдельное приложение. При потере сети появится офлайн-экран; рабочие данные намеренно не кэшируются."}
        ]
    },
    {
        "slug": "equipment-workflows",
        "title": "Работа с оборудованием",
        "summary": "Создание, редактирование, перенос, осмотр, фото и удаление карточек.",
        "category": "Руководство",
        "icon": "□",
        "order": 30,
        "sections": [
            {"title": "Добавление", "text": "Откройте «Добавить объект», выберите организацию и дождитесь загрузки её адресов. Название обязательно; серийный и инвентарный номера, количество и комментарий можно заполнить позже. Сначала используйте Dry-run."},
            {"title": "Редактирование", "text": "На вкладке «Обзор» измените реквизиты и сохраните. Ядро пробует PUT, затем PATCH. Передаётся набор заполненных полей. Фактически использованный маршрут показывается в уведомлении."},
            {"title": "Инвентаризация", "text": "На вкладке «Действия» выберите кампанию и отметьте объект найденным или не найденным. Для переноса выберите адрес-получатель. Осмотр сохраняет текстовый комментарий."},
            {"title": "Фото и распознавание", "text": "Загружайте изображения через вкладку «Фото». На вкладке «Серийник» доступен ручной поиск и распознавание номера по фотографии. Форматы определяются сервером F2C."},
            {"title": "Удаление", "text": "Опасная зона находится внизу вкладки «Обзор». Удаление необратимо. Проверьте карточку, сделайте Dry-run и только затем подтвердите операцию."}
        ]
    },
    {
        "slug": "script-examples",
        "title": "База примеров скриптов",
        "summary": "Готовые CLI, shell и Python-рецепты для автоматизации типовых операций.",
        "category": "Скрипты",
        "icon": "⌘",
        "order": 40,
        "sections": [
            {"title": "Авторизация и проверка сессии", "text": "Интерактивно сохраните сессию, затем проверьте текущего пользователя.", "code": "python3 f2c_inventory.py login\npython3 f2c_inventory.py status", "language": "bash"},
            {"title": "Поиск адреса", "text": "Интерактивный поиск удобен оператору, JSON — последующей обработке.", "code": "python3 f2c_inventory.py addr search \"Ленина\" --interactive\npython3 f2c_inventory.py addr list --org 10 --csv addresses.csv", "language": "bash"},
            {"title": "Экспорт реестра", "text": "Сохраните выборку по адресу в CSV или полный набор в JSON.", "code": "python3 f2c_inventory.py equipment list --address 101 --csv equipment.csv\npython3 f2c_inventory.py equipment list --all --json equipment.json", "language": "bash"},
            {"title": "Пакетное добавление", "text": "Сначала обязательно выполните предварительную проверку файла.", "code": "python3 f2c_inventory.py import equipment --csv equipment.csv --dry-run\npython3 f2c_inventory.py import equipment --csv equipment.csv", "language": "bash", "warning": "Названия флагов зависят от версии CLI. Выполните python3 f2c_inventory.py equipment --help перед массовой операцией."},
            {"title": "Резервная копия конфигурации", "text": "Копируйте карту API, но исключайте файл сессии из общих архивов.", "code": "mkdir -p backup\ncp ~/.config/f2c-inventory/api_map.json backup/api_map.json\nchmod 600 backup/api_map.json", "language": "bash"},
            {"title": "Python: уведомление о новых заявках", "text": "Готовый пример находится в репозитории. Переменные окружения позволяют запускать его из cron без изменения кода.", "code": "export F2C_BASE_URL=https://inventory.f2c.ru\npython3 examples/notify_new_requests.py", "language": "bash"},
            {"title": "Проверка API из Python", "text": "Минимальный безопасный пример использует сохранённую сессию и доменное ядро, а не вручную собранные URL.", "code": "from pathlib import Path\nimport f2c_inventory as core\nimport f2c_ops as ops\n\ncfg = Path.home() / '.config' / 'f2c-inventory'\napi_map = core.load_api_map(cfg)\nclient = core.Client(base_url='https://inventory.f2c.ru')\nauth = core.Auth(client, cfg)\nauth.load()\nrows = ops.equipment_rows(client, api_map, all_pages=True)\nprint(f'Оборудования: {len(rows)}')", "language": "python"}
        ]
    },
    {
        "slug": "api-and-security",
        "title": "API, сессии и безопасность",
        "summary": "Candidate trial, обновление токена, карта API и правила безопасной эксплуатации.",
        "category": "Администрирование",
        "icon": "⚙",
        "order": 50,
        "sections": [
            {"title": "Слои приложения", "text": "GUI вызывает f2c_ops, доменный слой выбирает операцию, а Client выполняет HTTP. Интерфейс не собирает маршруты самостоятельно, поэтому CLI и веб-приложение ведут себя одинаково."},
            {"title": "Candidate trial", "text": "Публичной схемы API нет. Ядро сначала использует api_map.json, затем типовые REST-маршруты. HTML-фолбэк, 404, 405 и NOT_FOUND пропускаются. Успешный маршрут показывается оператору."},
            {"title": "Обновление токена", "text": "При 401 или 403 Client однократно вызывает /api/auth/refresh, сохраняет новый access token и повторяет исходный запрос. Если refresh token недействителен, выполните вход заново."},
            {"title": "Хранение секретов", "text": "session.json создаётся с правами 0600. Не добавляйте его в Git, резервные копии общего доступа и логи. Для серверного запуска ограничьте доступ к F2C Hub средствами reverse proxy и TLS."},
            {"title": "Проверка состояния", "code": "ls -l ~/.config/f2c-inventory/session.json\npython3 f2c_inventory.py status\npython3 f2c_inventory.py recon --help", "language": "bash"}
        ]
    },
    {
        "slug": "troubleshooting",
        "title": "Диагностика и решение проблем",
        "summary": "Что делать при ошибках входа, NOT_FOUND, пустых списках и проблемах PWA.",
        "category": "Поддержка",
        "icon": "?",
        "order": 60,
        "sections": [
            {"title": "Не удаётся войти", "text": "Проверьте F2C_BASE_URL, учётные данные и доступность сервера. В демо-режиме пароль должен быть secret. Удалите только устаревшую локальную сессию и войдите снова."},
            {"title": "NOT_FOUND или METHOD_NOT_ALLOWED", "text": "Карта API не соответствует текущей версии сервиса. Постройте её заново из HAR рабочего браузерного сеанса и повторите операцию в Dry-run."},
            {"title": "Списки пусты", "text": "Убедитесь, что у пользователя есть права на организации и инвентаризации. Сбросьте фильтры реестра. Проверьте ответ через соответствующую CLI-команду."},
            {"title": "PWA не устанавливается", "text": "Для установки требуется HTTPS или localhost, корректный manifest и активный Service Worker. Обновите страницу, затем проверьте раздел Application в инструментах разработчика браузера."},
            {"title": "Сбор диагностической информации", "code": "python3 f2c_inventory.py status\npython3 f2c_inventory.py equipment list --json /tmp/f2c-check.json\nuvicorn webui.app:app --host 0.0.0.0 --port 8000 --log-level debug", "language": "bash", "warning": "Перед передачей диагностики удалите токены, персональные данные и сведения об оборудовании."}
        ]
    }
]


def connect(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("""CREATE TABLE IF NOT EXISTS articles (
        slug TEXT PRIMARY KEY, title TEXT NOT NULL, summary TEXT NOT NULL,
        category TEXT NOT NULL, icon TEXT NOT NULL, sort_order INTEGER NOT NULL,
        content TEXT NOT NULL, schema_version TEXT NOT NULL
    )""")
    current = db.execute("SELECT schema_version FROM articles LIMIT 1").fetchone()
    if not current or current[0] != SCHEMA_VERSION:
        db.execute("DELETE FROM articles")
        db.executemany(
            "INSERT INTO articles VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(a["slug"], a["title"], a["summary"], a["category"], a["icon"],
              a["order"], json.dumps(a["sections"], ensure_ascii=False),
              SCHEMA_VERSION) for a in ARTICLES])
        db.commit()
    return db


def list_articles(path: Path, query="", category=""):
    with connect(path) as db:
        sql = "SELECT slug,title,summary,category,icon FROM articles WHERE 1=1"
        args = []
        if category:
            sql += " AND category = ?"
            args.append(category)
        if query:
            sql += " AND (title LIKE ? OR summary LIKE ? OR content LIKE ?)"
            term = f"%{query}%"
            args.extend([term, term, term])
        sql += " ORDER BY sort_order, title"
        return [dict(row) for row in db.execute(sql, args)]


def get_article(path: Path, slug: str):
    with connect(path) as db:
        row = db.execute("SELECT * FROM articles WHERE slug = ?", (slug,)).fetchone()
        if not row:
            return None
        item = dict(row)
        item["sections"] = json.loads(item.pop("content"))
        return item


def categories(path: Path):
    with connect(path) as db:
        return [row[0] for row in db.execute(
            "SELECT category FROM articles GROUP BY category ORDER BY MIN(sort_order)")]
