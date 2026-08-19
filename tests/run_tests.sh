#!/usr/bin/env bash
#
# Тестовый набор для analyzer.sh (тест-кейсы раздела 4.1 ТЗ).
#
# Использование:  ./tests/run_tests.sh
#
# Требования: python3 (локальный HTTP-сервер), jq, curl, bash.
# Тесты, требующие внешнего интернета (GitHub API), пропускаются, если
# api.github.com недоступен.

set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ANALYZER="$ROOT/analyzer.sh"
SERVER_SCRIPT="$ROOT/tests/server.py"
OUT="$ROOT/tests/out"
# Свободный порт для тестового сервера (избегаем TIME_WAIT-коллизий)
PORT="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"
SERVER_PID=""

PASS=0
FAIL=0
SKIP=0
FAILED_TESTS=()

# --- helpers ----------------------------------------------------------------

ok() { PASS=$((PASS + 1)); echo "    PASS: $1"; }
bad() { FAIL=$((FAIL + 1)); FAILED_TESTS+=("$1"); echo "    FAIL: $1"; }
skip() { SKIP=$((SKIP + 1)); echo "    SKIP: $1"; }

check_rc() {  # check_rc <имя> <ожидаемый> <фактический>
    if [[ "$2" == "$3" ]]; then
        ok "$1"
    else
        bad "$1 (ожидался код $2, получен $3)"
    fi
}

check_eq() {  # check_eq <имя> <ожидаемое> <фактическое>
    if [[ "$2" == "$3" ]]; then
        ok "$1"
    else
        bad "$1 (ожидалось «$2», получено «$3»)"
    fi
}

check_contains() {  # check_contains <имя> <подстрока> <текст>
    if [[ "$3" == *"$2"* ]]; then
        ok "$1"
    else
        bad "$1 (не найдено «$2» в: ${3:0:120}...)"
    fi
}

json_get() {  # json_get <файл> <jq-фильтр>
    jq -r "$2" "$1" 2>/dev/null
}

latest_file() {  # latest_file <glob> — последний по имени
    # shellcheck disable=SC2086,SC2012 # $1 — glob-шаблон намеренно без кавычек
    ls -1 $1 2>/dev/null | tail -n1
}

start_server() {
    python3 "$SERVER_SCRIPT" "$PORT" >"$OUT/server.log" 2>&1 &
    SERVER_PID=$!
    local i
    # shellcheck disable=SC2034 # i — только счётчик цикла
    for i in $(seq 1 50); do
        if curl -s -o /dev/null "http://127.0.0.1:$PORT/"; then
            return 0
        fi
        sleep 0.1
    done
    echo "FATAL: не удалось запустить тестовый HTTP-сервер" >&2
    cat "$OUT/server.log" >&2
    exit 1
}

# shellcheck disable=SC2329 # вызывается косвенно (trap EXIT)
stop_server() {
    if [[ -n "$SERVER_PID" ]] && kill -0 "$SERVER_PID" 2>/dev/null; then
        kill "$SERVER_PID" 2>/dev/null || true
        wait "$SERVER_PID" 2>/dev/null || true
    fi
}

net_ok() {  # доступен ли api.github.com
    curl -s -o /dev/null -m 8 --connect-timeout 5 "https://api.github.com/" 2>/dev/null
}

# shellcheck disable=SC2329 # вызывается косвенно (trap EXIT)
cleanup() {
    stop_server
}
trap cleanup EXIT

# --- начало ----------------------------------------------------------------

echo "== Тестовый набор analyzer.sh =="
bash -n "$ANALYZER" || { echo "FATAL: синтаксическая ошибка в analyzer.sh" >&2; exit 1; }
command -v python3 >/dev/null || { echo "FATAL: нужен python3" >&2; exit 1; }
command -v jq >/dev/null || { echo "FATAL: нужен jq" >&2; exit 1; }

rm -rf "$OUT"
mkdir -p "$OUT"
start_server
echo "Сервер запущен на 127.0.0.1:$PORT (PID $SERVER_PID)"

# ===========================================================================
# T1. Валидный URL → успешный аудит, корректные timings и заголовки
# ===========================================================================
echo
echo "T1: валидный URL (http://127.0.0.1:$PORT/)"
mkdir -p "$OUT/t1"
"$ANALYZER" "http://127.0.0.1:$PORT/" -o "$OUT/t1" \
    >"$OUT/t1/stdout.txt" 2>"$OUT/t1/stderr.txt"
rc=$?
check_rc "T1: exit code 0" 0 "$rc"
REP="$(latest_file "$OUT/t1/report_*.json")"
check_eq "T1: report создан" "1" "$([ -n "$REP" ] && echo 1 || echo 0)"
check_eq "T1: status=success" "success" "$(json_get "$REP" '.status')"
check_eq "T1: http_code=200" "200" "$(json_get "$REP" '.http_code')"
check_eq "T1: server" "TestServer/1.0" "$(json_get "$REP" '.headers.server')"
check_eq "T1: content_type" "text/html; charset=utf-8" "$(json_get "$REP" '.headers.content_type')"
check_eq "T1: title" "Test Page" "$(json_get "$REP" '.content.title')"
check_eq "T1: hsts" "max-age=31536000; includeSubDomains; preload" \
    "$(json_get "$REP" '.headers.strict_transport_security')"
check_eq "T1: hsts.max_age" "31536000" "$(json_get "$REP" '.headers.hsts.max_age')"
check_eq "T1: hsts.preload" "true" "$(json_get "$REP" '.headers.hsts.preload')"
check_eq "T1: error=null" "null" "$(json_get "$REP" '.error')"
check_eq "T1: timestamp ISO8601" "1" "$(json_get "$REP" '.timestamp' | grep -cE '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$' || echo 0)"
DNS="$(json_get "$REP" '.timings.dns_resolution')"
TTFB="$(json_get "$REP" '.timings.ttfb')"
if jq -e '.timings.dns_resolution | type == "number"' "$REP" >/dev/null 2>&1 \
    && [[ "$DNS" =~ ^[0-9]+(\.[0-9]+)?$ ]]; then
    ok "T1: dns_resolution число ($DNS)"
else
    bad "T1: dns_resolution число ($DNS)"
fi
if jq -e '.timings.ttfb | type == "number"' "$REP" >/dev/null 2>&1 \
    && [[ "$TTFB" =~ ^[0-9]+(\.[0-9]+)?$ ]]; then
    ok "T1: ttfb число ($TTFB)"
else
    bad "T1: ttfb число ($TTFB)"
fi
PERM="$(stat -c %a "$REP" 2>/dev/null)"
check_eq "T1: права отчёта 600" "600" "$PERM"
PERM="$(stat -c %a "$(latest_file "$OUT/t1/page_*.html")" 2>/dev/null)"
check_eq "T1: права HTML 644" "644" "$PERM"
PERM="$(stat -c %a "$(latest_file "$OUT/t1/headers_*.txt")" 2>/dev/null)"
check_eq "T1: права заголовков 600" "600" "$PERM"
check_eq "T1: file_paths заполнены" "1" \
    "$([ "$(json_get "$REP" '.file_paths.html')" != "null" ] && echo 1 || echo 0)"
check_contains "T1: в stdout сводка" "Результат аудита" "$(cat "$OUT/t1/stdout.txt")"

# ===========================================================================
# T2. URL без схемы → автоматически добавляется https://
# ===========================================================================
echo
echo "T2: URL без схемы"
mkdir -p "$OUT/t2"
"$ANALYZER" "example.invalid" -o "$OUT/t2" --no-notification --connect-timeout 3 --timeout 3 \
    >"$OUT/t2/stdout.txt" 2>"$OUT/t2/stderr.txt"
rc=$?
check_rc "T2: exit code 4 (нет сети для example.invalid)" 4 "$rc"
REP="$(latest_file "$OUT/t2/report_*.json")"
check_eq "T2: url получил https://" "https://example.invalid" "$(json_get "$REP" '.url')"
check_eq "T2: status=error" "error" "$(json_get "$REP" '.status')"
check_contains "T2: error.log содержит ERROR" "ERROR" "$(cat "$OUT/t2/error.log" 2>/dev/null)"

# ===========================================================================
# T3. Невалидный URL (пробелы) → код 3
# ===========================================================================
echo
echo "T3: невалидный URL (ht!tp://bad url)"
"$ANALYZER" "ht!tp://bad url" -o "$OUT/t3" >"$OUT/t3_stdout.txt" 2>"$OUT/t3_stderr.txt"
rc=$?
check_rc "T3: exit code 3" 3 "$rc"
check_contains "T3: сообщение об ошибке" "невалидный URL" "$(cat "$OUT/t3_stderr.txt")"

# ===========================================================================
# T4. Запрещённая схема → код 3
# ===========================================================================
echo
echo "T4: запрещённая схема"
for badurl in "file:///etc/passwd" "ftp://example.com/x" "javascript:alert(1)"; do
    "$ANALYZER" "$badurl" -o "$OUT/t4" >"$OUT/t4_stdout.txt" 2>"$OUT/t4_stderr.txt"
    rc=$?
    check_rc "T4: $badurl → exit 3" 3 "$rc"
    check_contains "T4: $badurl → сообщение о схеме" "схема" "$(cat "$OUT/t4_stderr.txt")"
done

# ===========================================================================
# T5. Сетевой таймаут → код 4, запись в error.log
# ===========================================================================
echo
echo "T5: сетевой таймаут (10.255.255.1)"
mkdir -p "$OUT/t5"
"$ANALYZER" "http://10.255.255.1/" -o "$OUT/t5" --no-notification \
    --connect-timeout 1 --timeout 2 \
    >"$OUT/t5/stdout.txt" 2>"$OUT/t5/stderr.txt"
rc=$?
check_rc "T5: exit code 4" 4 "$rc"
check_contains "T5: error.log содержит ERROR" "ERROR" "$(cat "$OUT/t5/error.log" 2>/dev/null)"
check_contains "T5: error.log содержит curl exit code" "curl exit code" "$(cat "$OUT/t5/error.log" 2>/dev/null)"

# ===========================================================================
# T6. HTTP 404 → аудит выполнен, status=partial
# ===========================================================================
echo
echo "T6: HTTP 404"
mkdir -p "$OUT/t6"
"$ANALYZER" "http://127.0.0.1:$PORT/404" -o "$OUT/t6" >"$OUT/t6/stdout.txt" 2>&1
rc=$?
check_rc "T6: exit code 0" 0 "$rc"
REP="$(latest_file "$OUT/t6/report_*.json")"
check_eq "T6: http_code=404" "404" "$(json_get "$REP" '.http_code')"
check_eq "T6: status=partial" "partial" "$(json_get "$REP" '.status')"
check_eq "T6: title анализируется" "Not Found" "$(json_get "$REP" '.content.title')"

# ===========================================================================
# T7. Отсутствие <title> → content.title = null
# ===========================================================================
echo
echo "T7: отсутствие <title>"
mkdir -p "$OUT/t7"
"$ANALYZER" "http://127.0.0.1:$PORT/notitle" -o "$OUT/t7" >/dev/null 2>&1
rc=$?
check_rc "T7: exit code 0" 0 "$rc"
REP="$(latest_file "$OUT/t7/report_*.json")"
check_eq "T7: title=null" "null" "$(json_get "$REP" '.content.title')"

# ===========================================================================
# T8. Отсутствие Strict-Transport-Security → null
# ===========================================================================
echo
echo "T8: отсутствие HSTS"
mkdir -p "$OUT/t8"
"$ANALYZER" "http://127.0.0.1:$PORT/noheaders" -o "$OUT/t8" >/dev/null 2>&1
rc=$?
check_rc "T8: exit code 0" 0 "$rc"
REP="$(latest_file "$OUT/t8/report_*.json")"
check_eq "T8: hsts=null" "null" "$(json_get "$REP" '.headers.strict_transport_security')"
check_eq "T8: server=null" "null" "$(json_get "$REP" '.headers.server')"
check_eq "T8: content_length=null" "null" "$(json_get "$REP" '.headers.content_length')"

# ===========================================================================
# T9. GUI-режим без termux-api → текстовый режим с предупреждением
# ===========================================================================
echo
echo "T9: GUI без termux-api (текстовый режим)"
mkdir -p "$OUT/t9"
printf 'http://127.0.0.1:%s/\nn\n' "$PORT" | "$ANALYZER" -o "$OUT/t9" \
    >"$OUT/t9/stdout.txt" 2>"$OUT/t9/stderr.txt"
rc=$?
check_rc "T9: exit code 0" 0 "$rc"
check_contains "T9: предупреждение в error.log" "termux-dialog не найден" "$(cat "$OUT/t9/error.log" 2>/dev/null)"
check_contains "T9: сводка в stdout" "Результат аудита" "$(cat "$OUT/t9/stdout.txt")"
REP="$(latest_file "$OUT/t9/report_*.json")"
check_eq "T9: аудит выполнен" "success" "$(json_get "$REP" '.status')"

# stdin закрыт и URL не передан → понятная ошибка
"$ANALYZER" -o "$OUT/t9" </dev/null >"$OUT/t9/stdout2.txt" 2>"$OUT/t9/stderr2.txt"
rc=$?
check_rc "T9b: stdin закрыт → код 1" 1 "$rc"
check_contains "T9b: сообщение об ошибке" "не указан URL" "$(cat "$OUT/t9/stderr2.txt")"

# ===========================================================================
# T10. Gist без токена → код 5
# ===========================================================================
echo
echo "T10: Gist без токена"
mkdir -p "$OUT/t10"
env -u GITHUB_TOKEN -u GH_TOKEN \
    "$ANALYZER" "http://127.0.0.1:$PORT/" --gist -o "$OUT/t10" \
    >"$OUT/t10/stdout.txt" 2>"$OUT/t10/stderr.txt"
rc=$?
check_rc "T10: exit code 5" 5 "$rc"
check_contains "T10: сообщение о GITHUB_TOKEN" "GITHUB_TOKEN" "$(cat "$OUT/t10/stderr.txt")"
check_contains "T10: error.log содержит ERROR" "ERROR" "$(cat "$OUT/t10/error.log" 2>/dev/null)"

# ===========================================================================
# T11. Gist с неверным токеном → код 5 (нужен доступ к api.github.com)
# ===========================================================================
echo
echo "T11: Gist с неверным токеном"
mkdir -p "$OUT/t11"
if net_ok; then
    GITHUB_TOKEN="ghp_invalid_token_for_testing_1234567890" \
        "$ANALYZER" "http://127.0.0.1:$PORT/" --gist -o "$OUT/t11" \
        >"$OUT/t11/stdout.txt" 2>"$OUT/t11/stderr.txt"
    rc=$?
    check_rc "T11: exit code 5" 5 "$rc"
    check_contains "T11: сообщение об ошибке авторизации" "GitHub API" "$(cat "$OUT/t11/stderr.txt")"
    check_contains "T11: error.log содержит ERROR" "ERROR" "$(cat "$OUT/t11/error.log" 2>/dev/null)"
else
    skip "T11: api.github.com недоступен"
fi

# ===========================================================================
# T12. Превышение --max-size → загрузка прервана, код 4, сообщение
# ===========================================================================
echo
echo "T12: превышение --max-size"
mkdir -p "$OUT/t12"
"$ANALYZER" "http://127.0.0.1:$PORT/big" --max-size 1024 -o "$OUT/t12" \
    >"$OUT/t12/stdout.txt" 2>"$OUT/t12/stderr.txt"
rc=$?
check_rc "T12: exit code 4" 4 "$rc"
check_contains "T12: сообщение о лимите" "лимит" "$(cat "$OUT/t12/stderr.txt" "$OUT/t12/stdout.txt")"
check_contains "T12: error.log содержит ERROR" "ERROR" "$(cat "$OUT/t12/error.log" 2>/dev/null)"

# ===========================================================================
# T13. Параллельный запуск → уникальные имена файлов
# ===========================================================================
echo
echo "T13: параллельный запуск двух экземпляров"
mkdir -p "$OUT/t13"
"$ANALYZER" "http://127.0.0.1:$PORT/" -o "$OUT/t13" >/dev/null 2>&1 &
PID1=$!
"$ANALYZER" "http://127.0.0.1:$PORT/404" -o "$OUT/t13" >/dev/null 2>&1 &
PID2=$!
wait "$PID1"; RC1=$?
wait "$PID2"; RC2=$?
check_rc "T13: оба завершились успешно" 0 "$((RC1 + RC2))"
# shellcheck disable=SC2012 # простой glob, ls достаточен
NREP="$(ls -1 "$OUT/t13"/report_*.json 2>/dev/null | wc -l)"
# shellcheck disable=SC2012 # простой glob, ls достаточен
NPAGE="$(ls -1 "$OUT/t13"/page_*.html 2>/dev/null | wc -l)"
check_eq "T13: два отчёта" "2" "$NREP"
check_eq "T13: две HTML-копии" "2" "$NPAGE"
NAMES="$(ls -1 "$OUT/t13"/report_*.json)"
# shellcheck disable=SC2015 # ok/bad не завершаются ошибкой
[[ "$(printf '%s\n' "$NAMES" | sort -u | wc -l)" == "2" ]] && ok "T13: имена уникальны" \
    || bad "T13: имена файлов совпадают"

# ===========================================================================
# T14. Ctrl+C во время запроса → временные файлы удалены, сообщение
# ===========================================================================
echo
echo "T14: SIGINT во время запроса"
mkdir -p "$OUT/t14"
rm -rf /tmp/analyzer.* 2>/dev/null
# setsid: свой процесс и группа процессов. SIGTERM (тот же обработчик, что и
# SIGINT/Ctrl+C в интерактивном Termux: trap INT TERM) шлём всей группе,
# чтобы сигнал дошёл и до curl.
setsid "$ANALYZER" "http://127.0.0.1:$PORT/slow" -o "$OUT/t14" --no-notification \
    >"$OUT/t14/stdout.txt" 2>"$OUT/t14/stderr.txt" &
PID=$!
sleep 1
kill -TERM -"$PID" 2>/dev/null
wait "$PID"; rc=$?
check_rc "T14: exit code 130" 130 "$rc"
check_contains "T14: сообщение о прерывании" "Прервано" "$(cat "$OUT/t14/stderr.txt")"
# shellcheck disable=SC2012 # простой glob, ls достаточен
LEFT="$(ls -1d /tmp/analyzer.* 2>/dev/null | wc -l)"
check_eq "T14: временные директории удалены" "0" "$LEFT"

# ===========================================================================
# T15. Отсутствие jq → ручная генерация JSON, валидный результат
# ===========================================================================
echo
echo "T15: без jq (ручная генерация JSON)"
FAKEBIN="$OUT/t15/fakebin"
mkdir -p "$FAKEBIN" "$OUT/t15"
for f in /usr/bin/*; do
    b="$(basename "$f")"
    [[ "$b" == "jq" ]] && continue
    ln -sf "$f" "$FAKEBIN/$b" 2>/dev/null
done
# PATH только из fakebin (без jq), чтобы command -v jq не нашёл реальный jq
PATH="$FAKEBIN" "$ANALYZER" "http://127.0.0.1:$PORT/" -o "$OUT/t15" \
    >"$OUT/t15/stdout.txt" 2>"$OUT/t15/stderr.txt"
rc=$?
check_rc "T15: exit code 0" 0 "$rc"
REP="$(latest_file "$OUT/t15/report_*.json")"
# валидация JSON средствами python3
# shellcheck disable=SC2015 # ok/bad не завершаются ошибкой
python3 -m json.tool "$REP" >/dev/null 2>&1 && ok "T15: JSON валиден (python -m json.tool)" \
    || bad "T15: JSON невалиден"
check_eq "T15: url корректен" "http://127.0.0.1:$PORT/" "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["url"])' "$REP")"
check_eq "T15: title корректен" "Test Page" "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["content"]["title"])' "$REP")"
check_contains "T15: предупреждение о jq" "jq не найден" "$(cat "$OUT/t15/error.log" 2>/dev/null)"

# ===========================================================================
# Дополнительные проверки
# ===========================================================================

echo
echo "D1: редиректы (по умолчанию не следуем)"
mkdir -p "$OUT/d1"
"$ANALYZER" "http://127.0.0.1:$PORT/redirect" -o "$OUT/d1" >/dev/null 2>&1
rc=$?
check_rc "D1: exit code 0" 0 "$rc"
REP="$(latest_file "$OUT/d1/report_*.json")"
check_eq "D1: http_code=302" "302" "$(json_get "$REP" '.http_code')"
check_eq "D1: followed=false" "false" "$(json_get "$REP" '.redirects.followed')"
check_eq "D1: count=0" "0" "$(json_get "$REP" '.redirects.count')"

echo
echo "D2: --follow-redirects"
mkdir -p "$OUT/d2"
"$ANALYZER" "http://127.0.0.1:$PORT/redirect-chain" --follow-redirects -o "$OUT/d2" >/dev/null 2>&1
rc=$?
check_rc "D2: exit code 0" 0 "$rc"
REP="$(latest_file "$OUT/d2/report_*.json")"
check_eq "D2: http_code=200" "200" "$(json_get "$REP" '.http_code')"
check_eq "D2: count=2" "2" "$(json_get "$REP" '.redirects.count')"
check_eq "D2: followed=true" "true" "$(json_get "$REP" '.redirects.followed')"
check_contains "D2: final_url=/" "/" "$(json_get "$REP" '.redirects.final_url')"

echo
echo "D3: редирект на file:// при --follow-redirects блокируется"
mkdir -p "$OUT/d3"
"$ANALYZER" "http://127.0.0.1:$PORT/redirect-external" --follow-redirects -o "$OUT/d3" >/dev/null 2>&1
rc=$?
check_rc "D3: exit code 4 (протокол заблокирован)" 4 "$rc"

echo
echo "D4: декодирование HTML-сущностей в <title>"
mkdir -p "$OUT/d4"
"$ANALYZER" "http://127.0.0.1:$PORT/entities" -o "$OUT/d4" >/dev/null 2>&1
rc=$?
check_rc "D4: exit code 0" 0 "$rc"
REP="$(latest_file "$OUT/d4/report_*.json")"
EXPECTED="$(python3 -c 'import html; print(html.unescape("A &amp; B &lt; C &gt; D &quot;Q&quot; &nbsp; end"))')"
check_eq "D4: title декодирован" "$EXPECTED" "$(json_get "$REP" '.content.title')"

echo
echo "D5: многострочный <title>"
mkdir -p "$OUT/d5"
"$ANALYZER" "http://127.0.0.1:$PORT/multiline" -o "$OUT/d5" >/dev/null 2>&1
rc=$?
check_rc "D5: exit code 0" 0 "$rc"
REP="$(latest_file "$OUT/d5/report_*.json")"
check_eq "D5: title=Multi line" "Multi line" "$(json_get "$REP" '.content.title')"

echo
echo "D6: UTF-8 <title>"
mkdir -p "$OUT/d6"
"$ANALYZER" "http://127.0.0.1:$PORT/utf8" -o "$OUT/d6" >/dev/null 2>&1
rc=$?
check_rc "D6: exit code 0" 0 "$rc"
REP="$(latest_file "$OUT/d6/report_*.json")"
check_eq "D6: title=Тестовая страница" "Тестовая страница" "$(json_get "$REP" '.content.title')"

echo
echo "D7: сжатое тело (--compressed)"
mkdir -p "$OUT/d7"
"$ANALYZER" "http://127.0.0.1:$PORT/gzip" -o "$OUT/d7" >/dev/null 2>&1
rc=$?
check_rc "D7: exit code 0" 0 "$rc"
REP="$(latest_file "$OUT/d7/report_*.json")"
check_eq "D7: compressed=true" "true" "$(json_get "$REP" '.content.compressed')"
SB="$(json_get "$REP" '.content.size_bytes')"
ST="$(json_get "$REP" '.content.size_transferred')"
if (( SB > ST )); then ok "D7: размер файла > переданного ($SB > $ST)"; else bad "D7: размер файла > переданного ($SB vs $ST)"; fi

echo
echo "D8: --json-only без --output-dir (только JSON в stdout, файлов нет)"
mkdir -p "$OUT/d8/work"
(
    cd "$OUT/d8/work" || exit 1
    "$ANALYZER" "http://127.0.0.1:$PORT/" --json-only >report.json 2>stderr.txt
)
rc=$?
check_rc "D8: exit code 0" 0 "$rc"
# shellcheck disable=SC2015 # ok/bad не завершаются ошибкой
python3 -m json.tool "$OUT/d8/work/report.json" >/dev/null 2>&1 && ok "D8: stdout — валидный JSON" \
    || bad "D8: stdout — не JSON"
check_eq "D8: http_code в JSON" "200" "$(json_get "$OUT/d8/work/report.json" '.http_code')"
# shellcheck disable=SC2012 # простой glob, ls достаточен
NFILES="$(ls -1 "$OUT/d8/work"/page_*.html "$OUT/d8/work"/headers_*.txt "$OUT/d8/work"/report_*.json 2>/dev/null | wc -l)"
check_eq "D8: файлы не сохранены" "0" "$NFILES"
check_eq "D8: file_paths.html=null" "null" "$(json_get "$OUT/d8/work/report.json" '.file_paths.html')"

echo
echo "D9: --json-only с --output-dir (JSON в stdout + файлы)"
mkdir -p "$OUT/d9"
"$ANALYZER" "http://127.0.0.1:$PORT/" --json-only -o "$OUT/d9" >"$OUT/d9/stdout.json" 2>/dev/null
rc=$?
check_rc "D9: exit code 0" 0 "$rc"
# shellcheck disable=SC2015 # ok/bad не завершаются ошибкой
python3 -m json.tool "$OUT/d9/stdout.json" >/dev/null 2>&1 && ok "D9: stdout — валидный JSON" \
    || bad "D9: stdout — не JSON"
# shellcheck disable=SC2012 # простой glob, ls достаточен
NREP="$(ls -1 "$OUT/d9"/report_*.json 2>/dev/null | wc -l)"
check_eq "D9: отчёт сохранён" "1" "$NREP"

echo
echo "D10: HTTP 500 → status=partial"
mkdir -p "$OUT/d10"
"$ANALYZER" "http://127.0.0.1:$PORT/500" -o "$OUT/d10" >/dev/null 2>&1
rc=$?
check_rc "D10: exit code 0" 0 "$rc"
REP="$(latest_file "$OUT/d10/report_*.json")"
check_eq "D10: http_code=500" "500" "$(json_get "$REP" '.http_code')"
check_eq "D10: status=partial" "partial" "$(json_get "$REP" '.status')"

echo
echo "D11: отсутствие Content-Type"
mkdir -p "$OUT/d11"
"$ANALYZER" "http://127.0.0.1:$PORT/nocontenttype" -o "$OUT/d11" >/dev/null 2>&1
rc=$?
check_rc "D11: exit code 0" 0 "$rc"
REP="$(latest_file "$OUT/d11/report_*.json")"
check_eq "D11: content_type=null" "null" "$(json_get "$REP" '.headers.content_type')"

echo
echo "D12: --help и --version"
"$ANALYZER" --help >"$OUT/help.txt" 2>&1
check_rc "D12: --help → exit 0" 0 "$?"
check_contains "D12: --help содержит Использование" "Использование" "$(cat "$OUT/help.txt")"
"$ANALYZER" --version >"$OUT/version.txt" 2>&1
check_rc "D12: --version → exit 0" 0 "$?"
check_contains "D12: --version содержит версию" "1.1.0" "$(cat "$OUT/version.txt")"

echo
echo "D13: IPv6-адрес без скобок → автодополнение https:// + скобки"
mkdir -p "$OUT/d13"
"$ANALYZER" "2001:db8::1" -o "$OUT/d13" --no-notification --connect-timeout 1 --timeout 2 \
    >/dev/null 2>&1
rc=$?
check_rc "D13: exit code 4 (сеть недоступна)" 4 "$rc"
REP="$(latest_file "$OUT/d13/report_*.json")"
check_eq "D13: url нормализован" "https://[2001:db8::1]" "$(json_get "$REP" '.url')"

echo
echo "D14: реальный HTTPS-аудит (github.com), если доступен"
mkdir -p "$OUT/d14"
if curl -s -o /dev/null -m 8 --connect-timeout 5 "https://github.com/"; then
    "$ANALYZER" "github.com" --follow-redirects -o "$OUT/d14" >/dev/null 2>&1
    rc=$?
    check_rc "D14: exit code 0" 0 "$rc"
    REP="$(latest_file "$OUT/d14/report_*.json")"
    check_eq "D14: url с https://" "https://github.com" "$(json_get "$REP" '.url')"
    check_contains "D14: http_code 2xx/3xx" "2" "$(json_get "$REP" '.http_code' | cut -c1)"
    check_eq "D14: tls_handshake > 0" "1" "$([ "$(json_get "$REP" '.timings.tls_handshake')" != "0.000" ] && echo 1 || echo 0)"
else
    skip "D14: github.com недоступен"
fi

echo
echo "D15: --debug (трассировка set -x)"
mkdir -p "$OUT/d15"
"$ANALYZER" "http://127.0.0.1:$PORT/" --debug -o "$OUT/d15" \
    >"$OUT/d15/stdout.txt" 2>"$OUT/d15/stderr.txt"
rc=$?
check_rc "D15: exit code 0" 0 "$rc"
check_contains "D15: трассировка в stderr" "+ " "$(cat "$OUT/d15/stderr.txt")"
REP="$(latest_file "$OUT/d15/report_*.json")"
check_eq "D15: отчёт корректен" "success" "$(json_get "$REP" '.status')"

echo
echo "D15b: короткий флаг -d"
"$ANALYZER" -d "http://127.0.0.1:$PORT/" -o "$OUT/d15" \
    >/dev/null 2>"$OUT/d15/stderr_b.txt"
check_contains "D15b: -d включает трассировку" "+ " "$(cat "$OUT/d15/stderr_b.txt")"

echo
echo "D16: сводка с таблицей метрик и pretty-JSON"
mkdir -p "$OUT/d16"
"$ANALYZER" "http://127.0.0.1:$PORT/" -o "$OUT/d16" >"$OUT/d16/stdout.txt" 2>/dev/null
rc=$?
check_rc "D16: exit code 0" 0 "$rc"
check_contains "D16: заголовок таблицы" "МЕТРИКИ СОЕДИНЕНИЯ" "$(cat "$OUT/d16/stdout.txt")"
check_contains "D16: строка DNS" "DNS (резолв)" "$(cat "$OUT/d16/stdout.txt")"
check_contains "D16: строка TTFB" "TTFB (первый байт)" "$(cat "$OUT/d16/stdout.txt")"
check_contains "D16: заголовок JSON-отчёта" "JSON-отчёт" "$(cat "$OUT/d16/stdout.txt")"
check_contains "D16: pretty JSON в stdout" '"timings"' "$(cat "$OUT/d16/stdout.txt")"

echo
echo "D17: --debug — лог ошибки с номером строки"
mkdir -p "$OUT/d17"
"$ANALYZER" "http://10.255.255.1/" --debug --no-notification \
    --connect-timeout 1 --timeout 2 -o "$OUT/d17" \
    >"$OUT/d17/stdout.txt" 2>"$OUT/d17/stderr.txt"
rc=$?
check_rc "D17: exit code 4" 4 "$rc"
check_contains "D17: номер строки ошибки" "Ошибка в строке" "$(cat "$OUT/d17/stderr.txt")"
check_contains "D17: код возврата в логе" "Код возврата" "$(cat "$OUT/d17/stderr.txt")"

echo
echo "D18: сообщение о проверке зависимостей"
check_contains "D18: заголовок проверки" "Проверка зависимостей" "$(cat "$OUT/d16/stdout.txt")"
# окружение-зависимо: либо всё установлено, либо перечислены недостающие
if grep -q "Все зависимости установлены" "$OUT/d16/stdout.txt" \
    || grep -q "Отсутствуют" "$OUT/d16/stdout.txt"; then
    ok "D18: итог проверки зависимостей"
else
    bad "D18: итог проверки зависимостей"
fi

echo
echo "D19: нет ANSI-кодов при выводе в файл"
if grep -qP $'\x1b\[' "$OUT/d16/stdout.txt"; then
    bad "D19: найдены ANSI-последовательности в stdout"
else
    ok "D19: нет ANSI-кодов"
fi
if grep -qP $'\x1b\[' "$OUT/d15/stderr.txt"; then
    bad "D19: найдены ANSI-последовательности в stderr"
else
    ok "D19: нет ANSI-кодов в stderr"
fi

# ===========================================================================
# Итог
# ===========================================================================
echo
echo "=============================================="
echo "Итог: PASS=$PASS FAIL=$FAIL SKIP=$SKIP"
if (( FAIL > 0 )); then
    printf 'Проваленные проверки: %s\n' "${FAILED_TESTS[*]}"
    exit 1
fi
echo "Все проверки пройдены."
exit 0
