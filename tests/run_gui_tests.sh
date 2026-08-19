#!/usr/bin/env bash
#
# Смоук-тесты веб-GUI (gui/server.py) для analyzer.sh.
#
# Использование:  ./tests/run_gui_tests.sh
# Требования: python3, curl, jq, bash.

set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$ROOT/tests/out/gui"
GUI_PORT="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')"
SRV_PORT="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')"
GUI_PID=""
SRV_PID=""

PASS=0
FAIL=0
FAILED_TESTS=()

ok() { PASS=$((PASS + 1)); echo "    PASS: $1"; }
bad() { FAIL=$((FAIL + 1)); FAILED_TESTS+=("$1"); echo "    FAIL: $1"; }

check_eq() {  # check_eq <имя> <ожидаемое> <фактическое>
    if [[ "$2" == "$3" ]]; then ok "$1"; else bad "$1 (ожидалось «$2», получено «$3»)"; fi
}

check_contains() {  # check_contains <имя> <подстрока> <текст>
    if [[ "$3" == *"$2"* ]]; then ok "$1"; else bad "$1 (не найдено «$2»)"; fi
}

# shellcheck disable=SC2329 # вызывается косвенно (trap EXIT)
cleanup() {
    [[ -n "$GUI_PID" ]] && kill "$GUI_PID" 2>/dev/null
    [[ -n "$SRV_PID" ]] && kill "$SRV_PID" 2>/dev/null
    wait 2>/dev/null || true
}
trap cleanup EXIT

wait_port() {  # wait_port <порт>
    local i
    # shellcheck disable=SC2034
    for i in $(seq 1 50); do
        if curl -s -o /dev/null "http://127.0.0.1:$1/"; then return 0; fi
        sleep 0.1
    done
    return 1
}

echo "== Смоук-тесты веб-GUI =="
command -v jq >/dev/null || { echo "FATAL: нужен jq" >&2; exit 1; }
mkdir -p "$OUT"

python3 "$ROOT/tests/server.py" "$SRV_PORT" >"$OUT/testsrv.log" 2>&1 &
SRV_PID=$!
python3 "$ROOT/gui/server.py" --host 127.0.0.1 --port "$GUI_PORT" >"$OUT/gui.log" 2>&1 &
GUI_PID=$!
wait_port "$SRV_PORT" || { echo "FATAL: тестовый HTTP-сервер не поднялся" >&2; exit 1; }
wait_port "$GUI_PORT" || { echo "FATAL: GUI-сервер не поднялся" >&2; cat "$OUT/gui.log" >&2; exit 1; }
echo "GUI: 127.0.0.1:$GUI_PORT, тестовый сервер: 127.0.0.1:$SRV_PORT"

echo
echo "G1: GET / отдаёт интерфейс"
HTML="$(curl -s "http://127.0.0.1:$GUI_PORT/")"
check_contains "G1: страница содержит форму" "id=\"form\"" "$HTML"
check_contains "G1: заголовок" "analyzer.sh" "$HTML"

echo
echo "G2: GET /api/health"
H="$(curl -s "http://127.0.0.1:$GUI_PORT/api/health")"
check_eq "G2: ok=true" "true" "$(jq -r '.ok' <<<"$H")"
check_eq "G2: analyzer найден" "true" "$(jq -r '.analyzer' <<<"$H")"

echo
echo "G3: успешный аудит через API"
R="$(curl -s -X POST "http://127.0.0.1:$GUI_PORT/api/audit" \
    -H 'Content-Type: application/json' \
    -d "{\"url\":\"http://127.0.0.1:$SRV_PORT/\",\"timeout\":10}")"
check_eq "G3: ok=true" "true" "$(jq -r '.ok' <<<"$R")"
check_eq "G3: exit_code=0" "0" "$(jq -r '.exit_code' <<<"$R")"
check_eq "G3: status=success" "success" "$(jq -r '.report.status' <<<"$R")"
check_eq "G3: http_code=200" "200" "$(jq -r '.report.http_code' <<<"$R")"
check_eq "G3: title" "Test Page" "$(jq -r '.report.content.title' <<<"$R")"
check_eq "G3: артефакты не сохраняются" "null" "$(jq -r '.report.file_paths.html' <<<"$R")"

echo
echo "G4: HTTP 404 → partial"
R="$(curl -s -X POST "http://127.0.0.1:$GUI_PORT/api/audit" \
    -H 'Content-Type: application/json' \
    -d "{\"url\":\"http://127.0.0.1:$SRV_PORT/404\",\"timeout\":10}")"
check_eq "G4: status=partial" "partial" "$(jq -r '.report.status' <<<"$R")"
check_eq "G4: http_code=404" "404" "$(jq -r '.report.http_code' <<<"$R")"

echo
echo "G5: запрещённая схема → exit 3, отчёта нет"
R="$(curl -s -X POST "http://127.0.0.1:$GUI_PORT/api/audit" \
    -H 'Content-Type: application/json' \
    -d '{"url":"file:///etc/passwd"}')"
check_eq "G5: ok=false" "false" "$(jq -r '.ok' <<<"$R")"
check_eq "G5: exit_code=3" "3" "$(jq -r '.exit_code' <<<"$R")"

echo
echo "G6: пустой URL → HTTP 400"
CODE="$(curl -s -o "$OUT/g6.json" -w '%{http_code}' \
    -X POST "http://127.0.0.1:$GUI_PORT/api/audit" \
    -H 'Content-Type: application/json' -d '{"url":""}')"
check_eq "G6: HTTP 400" "400" "$CODE"
check_contains "G6: сообщение об ошибке" "Не указан URL" "$(cat "$OUT/g6.json")"

echo
echo "G7: некорректный JSON → HTTP 400"
CODE="$(curl -s -o /dev/null -w '%{http_code}' \
    -X POST "http://127.0.0.1:$GUI_PORT/api/audit" \
    -H 'Content-Type: application/json' -d 'не json')"
check_eq "G7: HTTP 400" "400" "$CODE"

echo
echo "G8: неизвестный маршрут → 404"
CODE="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$GUI_PORT/nope")"
check_eq "G8: HTTP 404" "404" "$CODE"

echo
echo "G9: --follow-redirects через API"
R="$(curl -s -X POST "http://127.0.0.1:$GUI_PORT/api/audit" \
    -H 'Content-Type: application/json' \
    -d "{\"url\":\"http://127.0.0.1:$SRV_PORT/redirect-chain\",\"timeout\":10,\"follow_redirects\":true}")"
check_eq "G9: followed=true" "true" "$(jq -r '.report.redirects.followed' <<<"$R")"
check_eq "G9: count=2" "2" "$(jq -r '.report.redirects.count' <<<"$R")"
check_eq "G9: http_code=200" "200" "$(jq -r '.report.http_code' <<<"$R")"

echo
echo "=============================================="
echo "Итог: PASS=$PASS FAIL=$FAIL"
if (( FAIL > 0 )); then
    printf 'Проваленные проверки: %s\n' "${FAILED_TESTS[*]}"
    exit 1
fi
echo "Все проверки пройдены."
exit 0
