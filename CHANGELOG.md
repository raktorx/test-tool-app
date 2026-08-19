# Changelog

Все заметные изменения проекта `analyzer.sh` документируются в этом файле.

Формат основан на [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/),
проект придерживается [Semantic Versioning](https://semver.org/lang/ru/).

## [1.0.0] — 2026-08-19

### Добавлено

- Первая стабильная версия `analyzer.sh`.
- Два режима работы:
  - GUI-режим (по умолчанию): ввод URL, подтверждение загрузки на Gist
    и запрос токена через диалоги Termux:API (`termux-dialog`);
  - CLI/headless-режим: URL передаётся аргументом, поддержка опций
    `-u/--url`, `-t/--timeout`, `--connect-timeout`, `-o/--output-dir`,
    `--json-only`, `--no-notification`, `--gist`, `--gist-public`,
    `--max-size`, `--follow-redirects`, `--help`, `--version`.
- Валидация и нормализация URL:
  - автодополнение схемы `https://` при её отсутствии;
  - запрет не-HTTP схем (`file://`, `ftp://`, `javascript:` и др.);
  - удаление управляющих символов, отбраковка пробелов;
  - автооборачивание голых IPv6-адресов в квадратные скобки;
  - поддержка IDN (конвертация в punycode средствами curl).
- Сбор метрик одним запросом curl: DNS, TCP, TLS, TTFB, общее время
  (округление до 3 знаков), размер тела, HTTP-код, `Content-Type`,
  число редиректов и итоговый URL.
- Повторы запроса при transient-ошибках curl (коды 6, 7, 28, 35):
  до 2 повторов с задержкой 1 секунда.
- Анализ HTTP-заголовков: `Content-Type`, `Server`,
  `Strict-Transport-Security` (разбор `max-age`, `includeSubDomains`,
  `preload`), `Content-Length`, признак сжатия тела.
- Извлечение `<title>` (включая многострочные теги и HTML-сущности,
  декодирование через Python 3 при наличии).
- JSON-отчёт `report_YYYYMMDD_HHMMSS_PID.json`:
  - генерация через `jq -n --arg/--argjson`;
  - ручная генерация с валидацией (`python3 -m json.tool`) при отсутствии jq;
  - `status`: success / partial / error.
- Загрузка HTML-копии на GitHub Gist (secret по умолчанию,
  `--gist-public` для публичного; проверка лимита 10 МБ; таймаут 15 с;
  поле `gist_url` в отчёте).
- Логирование `error.log` с уровнями INFO/WARNING/ERROR и ротацией при 1 МБ;
  URL в логах очищаются от query-параметров, фрагментов и userinfo.
- Уведомления Termux с уникальным `--id` и снятием по завершении/прерывании.
- Обработка сигналов INT/TERM: удаление временных файлов, снятие
  уведомления, сообщение о прерывании (код 130).
- Права файлов: 600 для JSON и заголовков, 644 для HTML; уникальные имена
  файлов с PID-суффиксом (безопасно для параллельных запусков).
- Тестовый набор `tests/run_tests.sh` (локальный HTTP-сервер на Python 3,
  тест-кейсы раздела 4 ТЗ) и сервер `tests/server.py`.
- Документация: `README.md`, `LICENSE` (MIT).
