#!/usr/bin/env bash
#
# analyzer.sh — интерактивный Bash-скрипт для технического аудита веб-ресурсов
# в среде Termux (Android).
#
# Возможности:
#   * сбор метрик производительности (DNS, TCP, TLS, TTFB, общее время);
#   * анализ HTTP-заголовков и базового содержимого страницы (<title>);
#   * сохранение результатов в структурированном JSON-отчёте;
#   * загрузка HTML-копии страницы в GitHub Gist;
#   * интерактивный (GUI через Termux:API) и headless (CLI) режимы работы.
#
# Коды возврата: 0 — успех; 1 — общая ошибка; 2 — нет обязательных зависимостей;
#                3 — невалидный URL/запрещённая схема; 4 — сетевая ошибка/таймаут;
#                5 — ошибка GitHub API; 6 — ошибка генерации/сохранения отчёта.
#
# Лицензия: MIT (см. LICENSE).
#

set -o pipefail

SCRIPT_NAME="$(basename "$0")"
SCRIPT_VERSION="1.0.0"

# --- Конфигурация по умолчанию --------------------------------------------
TIMEOUT=30                            # --timeout        (сек)
CONNECT_TIMEOUT=10                    # --connect-timeout (сек)
OUTPUT_DIR="$PWD"                     # --output-dir
OUTPUT_DIR_SET=0                      # передан ли --output-dir явно
JSON_ONLY=0                           # --json-only
USE_NOTIFICATION=1                    # --no-notification
GIST_UPLOAD=0                         # --gist
GIST_PUBLIC=0                         # --gist-public
MAX_SIZE=$((10 * 1024 * 1024))        # --max-size (байт, 10 МБ)
FOLLOW_REDIRECTS=0                    # --follow-redirects
GUI_MODE=0                            # интерактивный режим (диалоги/read)
EXIT_CODE=0                           # итоговый код возврата скрипта
GIST_LIMIT_BYTES=$((10 * 1024 * 1024))   # лимит GitHub Gist на файл
LOG_ROTATE_BYTES=$((1024 * 1024))        # ротация error.log после 1 МБ
NOTIF_ID="audit_$$"                   # уникальный id уведомлений Termux:API
WORK_TMP=""                           # временная директория (mktemp -d)
LOG_FILE=""                           # путь к error.log

# --- Глобальное состояние (заполняется по ходу работы) ---------------------
URL=""                                # нормализованный URL (после валидации)
SCHEME=""                             # http | https
PERSIST_ARTIFACTS=1                   # сохраняются ли артефакты в OUTPUT_DIR
TIMESTAMP=""                          # ISO-8601 UTC
HTML_FILE=""
HEADERS_FILE=""
REPORT_FILE=""
REPORT_JSON=""
GITHUB_TOKEN="${GITHUB_TOKEN:-}"

HTTP_CODE=""                          # итоговый HTTP-код
HEADERS_HTTP_CODE=""                  # HTTP-код из файла заголовков (запасной)
CONTENT_TYPE=""
SERVER=""
HSTS=""
HSTS_MAX_AGE=""
HSTS_INCLUDE_SUBDOMAINS=false
HSTS_PRELOAD=false
CONTENT_LENGTH=""
COMPRESSED=false
TITLE=""
SIZE_DOWNLOAD=0                       # размер тела по данным curl (переданный)
SIZE_FILE=0                           # фактический размер сохранённого файла

DNS_RESOLUTION=0.000
TCP_CONNECTION=0.000
TLS_HANDSHAKE=0.000
TTFB=0.000
TOTAL=0.000
NUM_REDIRECTS=0
FINAL_URL=""
FOLLOWED=false

STATUS="success"
ERROR_MSG=""
CURL_EXIT_CODE=0                      # код возврата последнего вызова curl
GIST_URL=""

# ===========================================================================
# Служебные функции
# ===========================================================================

has_cmd() {
    command -v "$1" >/dev/null 2>&1
}

die() {                               # die <code> <сообщение...>
    local code="$1"
    shift
    printf 'Ошибка: %s\n' "$*" >&2
    exit "$code"
}

# --- Логирование -----------------------------------------------------------

log_msg() {                           # log_msg <УРОВЕНЬ> <сообщение>
    local level="$1" msg="$2"
    local entry sz
    entry="$(printf '[%s] %s: %s' "$(date '+%Y-%m-%d %H:%M:%S')" "$level" "$msg")"
    printf '%s\n' "$entry" >>"$LOG_FILE" 2>/dev/null || return 0
    # Ротация: если error.log больше 1 МБ — переименовать в error.log.1
    if [[ -f "$LOG_FILE" ]]; then
        sz="$(wc -c <"$LOG_FILE" 2>/dev/null || printf '0')"
        if (( sz > LOG_ROTATE_BYTES )); then
            mv -f "$LOG_FILE" "${LOG_FILE}.1" 2>/dev/null || true
        fi
    fi
}

log_info()  { log_msg "INFO"    "$1"; }
log_warn()  { log_msg "WARNING" "$1"; }
log_error() { log_msg "ERROR"   "$1"; }

# URL для логов: только схема+хост+путь (без query, фрагмента и userinfo).
sanitize_url() {
    local u="$1"
    u="${u%%\?*}"
    u="${u%%\#*}"
    if [[ "$u" =~ ^([a-zA-Z][a-zA-Z0-9+.-]*://)([^/@]+)@(.+)$ ]]; then
        printf '%s***@%s' "${BASH_REMATCH[1]}" "${BASH_REMATCH[3]}"
        return
    fi
    printf '%s' "$u"
}

host_of() {                           # хост из URL (для уведомлений)
    local u="$1"
    u="${u#*://}"
    u="${u%%[/?#]*}"
    u="${u##*@}"
    printf '%s' "$u"
}

# --- Уведомления Termux:API -------------------------------------------------

# shellcheck disable=SC2329 # вызывается косвенно (trap/cleanup)
notify_show() {                       # notify_show <заголовок> <текст>
    if (( USE_NOTIFICATION )) && has_cmd termux-notification; then
        termux-notification --id "$NOTIF_ID" --title "$1" --content "$2" \
            >/dev/null 2>&1 || true
    fi
}

# shellcheck disable=SC2329 # вызывается косвенно (trap/cleanup)
notify_dismiss() {
    if (( USE_NOTIFICATION )) && has_cmd termux-notification; then
        termux-notification --id "$NOTIF_ID" --remove >/dev/null 2>&1 || true
    fi
}

# --- Арифметика (округление до 3 знаков) ------------------------------------

round3() {                            # округлить число до 3 знаков
    local v="$1"
    if has_cmd awk; then
        awk -v x="$v" 'BEGIN { if (x == "" || x+0 == 0 && x != 0) x = 0; printf "%.3f", x }'
    elif has_cmd python3; then
        python3 -c 'import sys
try:
    v = float(sys.argv[1] or 0)
    print("%.3f" % v)
except Exception:
    print("0.000")' "$v" 2>/dev/null || printf '0.000'
    else
        printf '0.000'
    fi
}

timing_diff() {                       # разница a-b (не ниже 0), 3 знака
    local a="$1" b="$2"
    if has_cmd awk; then
        awk -v a="$a" -v b="$b" 'BEGIN { v = a - b; if (v < 0) v = 0; printf "%.3f", v }'
    elif has_cmd python3; then
        python3 -c 'import sys
try:
    v = float(sys.argv[1]) - float(sys.argv[2])
    print("%.3f" % max(v, 0.0))
except Exception:
    print("0.000")' "$a" "$b" 2>/dev/null || printf '0.000'
    else
        printf '0.000'
    fi
}

file_size() {                         # размер файла в байтах
    if has_cmd stat; then
        stat -c %s -- "$1" 2>/dev/null || wc -c <"$1" 2>/dev/null || printf '0'
    else
        wc -c <"$1" 2>/dev/null || printf '0'
    fi
}

# --- Проверка зависимостей --------------------------------------------------

check_deps() {
    local c missing=0
    local -a missing_list=()
    for c in bash curl grep sed mktemp date head tail tr cut mkdir mv rm wc basename dirname; do
        if ! has_cmd "$c"; then
            missing=1
            missing_list+=("$c")
        fi
    done
    if (( missing )); then
        printf 'Ошибка: отсутствуют обязательные зависимости: %s\n' "${missing_list[*]}" >&2
        printf 'Установите их в Termux: pkg install coreutils curl grep sed\n' >&2
        exit 2
    fi

    # Проверка версии curl (рекомендуется >= 7.68)
    local cver major minor
    cver="$(curl --version 2>/dev/null | head -n1 | sed -E 's/^curl ([0-9.]+).*/\1/')"
    major="${cver%%.*}"
    minor="$(printf '%s' "$cver" | cut -d. -f2)"
    if [[ "$cver" =~ ^[0-9]+(\.[0-9]+)?$ ]] && (( major < 7 )); then
        log_warn "curl $cver старше 7.x — возможны проблемы"
    elif [[ "$cver" =~ ^7\.[0-9]+ ]] && (( minor < 68 )); then
        log_warn "curl $cver старше рекомендуемой версии 7.68 — возможны проблемы"
    fi

    # Необязательные зависимости — деградация функциональности
    if ! has_cmd jq; then
        log_warn "jq не найден — JSON будет сгенерирован вручную"
    fi
    if ! has_cmd termux-notification; then
        log_warn "termux-notification не найден — системные уведомления отключены"
        USE_NOTIFICATION=0
    fi
    if ! has_cmd termux-dialog; then
        log_warn "termux-dialog не найден — GUI-диалоги недоступны (текстовый режим)"
    fi
}

# --- Валидация и нормализация URL -------------------------------------------

normalize_and_validate_url() {        # аргумент: исходный URL; результат в VALIDATED_URL
    local raw="$1"
    local scheme="" host="" colon_count=""

    # 1) удаляем управляющие символы (нормализация, раздел 2.12 ТЗ)
    raw="$(printf '%s' "$raw" | tr -d '\000-\010\013\014\016-\037\177')"

    # 2) пробельные символы недопустимы
    if [[ "$raw" =~ [[:space:]] ]]; then
        log_error "Невалидный URL (содержит пробелы): $(sanitize_url "$raw")"
        printf 'Ошибка: невалидный URL (содержит пробелы): %s\n' "$raw" >&2
        return 3
    fi
    if [[ -z "$raw" ]]; then
        log_error "Невалидный URL: пустое значение"
        printf 'Ошибка: URL не может быть пустым\n' >&2
        return 3
    fi

    # 3) определение схемы
    if [[ "$raw" =~ ^[a-zA-Z][a-zA-Z0-9+.-]*:// ]]; then
        scheme="${raw%%://*}"
        scheme="${scheme,,}"
        case "$scheme" in
            http|https) ;;
            *)
                log_error "Запрещённая схема URL: $scheme"
                printf 'Ошибка: запрещённая схема «%s» (разрешены только http и https)\n' "$scheme" >&2
                return 3
                ;;
        esac
    elif [[ "$raw" =~ ^[a-zA-Z][a-zA-Z0-9+.-]*: ]]; then
        # префикс со схемой без :// — например javascript:, file:, ftp:, data:
        scheme="${raw%%:*}"
        scheme="${scheme,,}"
        case "$scheme" in
            http|https)
                # "http:example.com" — опечатка, убираем схему и добавляем https://
                raw="${raw#*:}"
                raw="https://$raw"
                ;;
            *)
                log_error "Запрещённая схема URL: $scheme"
                printf 'Ошибка: запрещённая схема «%s» (разрешены только http и https)\n' "$scheme" >&2
                return 3
                ;;
        esac
    else
        # нет схемы — автодополнение https://
        raw="https://$raw"
        scheme="https"
    fi

    # 4) базовое соответствие шаблону URL и наличие хоста
    if [[ ! "$raw" =~ ^[a-zA-Z][a-zA-Z0-9+.-]*://[^[:space:]/?#]+(/[^[:space:]]*)?(\?[^[:space:]]*)?(#.*)?$ ]]; then
        log_error "Невалидный URL после нормализации: $(sanitize_url "$raw")"
        printf 'Ошибка: невалидный URL: %s\n' "$raw" >&2
        return 3
    fi

    # 5) IPv6: голый адрес без скобок (например 2001:db8::1) — заключаем в []
    host="${raw#*://}"
    host="${host%%[/?#]*}"
    if [[ "$host" != \[*\] && "$host" =~ ^[0-9A-Fa-f:.]+$ ]]; then
        colon_count="$(printf '%s' "$host" | tr -cd ':' | wc -c)"
        if [[ "$host" == *::* || colon_count -ge 2 ]]; then
            raw="${raw/:\/\/$host/://[$host]}"
        fi
    fi

    VALIDATED_URL="$raw"
    SCHEME="$scheme"
    return 0
}

# ===========================================================================
# Сбор метрик (один запрос curl с измерением фаз)
# ===========================================================================

is_retryable() {                      # коды curl, при которых делаем повтор
    case "$1" in
        6|7|28|35) return 0 ;;
        *) return 1 ;;
    esac
}

STATS_FORMAT='\n@@CURL_STATS@@\n%{time_namelookup}\t%{time_connect}\t%{time_appconnect}\t%{time_pretransfer}\t%{time_starttransfer}\t%{time_total}\t%{size_download}\t%{http_code}\t%{num_redirects}\t%{url_effective}\t%{content_type}'

run_audit() {
    local -a curl_args=()
    local max_redirs=0
    local attempt=1 max_attempts=3
    local out_file="" err_file="" stats_file=""
    local curl_rc=0

    (( FOLLOW_REDIRECTS )) && max_redirs=5

    # Все пользовательские данные передаются через массив аргументов
    # (защита от инъекций, раздел 2.12 ТЗ).
    curl_args=(
        --silent --show-error
        --connect-timeout "$CONNECT_TIMEOUT"
        --max-time "$TIMEOUT"
        --compressed
        --max-redirs "$max_redirs"
        --proto '=http,https'
        --proto-redir '=http,https'
        --url "$URL"
        -o "$HTML_FILE"
        -D "$HEADERS_FILE"
        -w "$STATS_FORMAT"
    )
    if (( FOLLOW_REDIRECTS )); then
        curl_args+=(-L)
    fi
    if (( MAX_SIZE > 0 )); then
        curl_args+=(--max-filesize "$MAX_SIZE")
    fi

    # Повторные попытки реализованы вручную (раздел 2.7 ТЗ): до 2 повторов
    # с задержкой 1 с при кодах curl 6, 7, 28, 35. Встроенный --retry curl
    # не используется, чтобы не было двойных повторов.
    while :; do
        out_file="$WORK_TMP/curl_out_${attempt}.txt"
        err_file="$WORK_TMP/curl_err_${attempt}.txt"
        curl "${curl_args[@]}" >"$out_file" 2>"$err_file"
        curl_rc=$?
        stats_file="$out_file"
        if (( curl_rc == 0 )) || (( attempt >= max_attempts )); then
            break
        fi
        if is_retryable "$curl_rc"; then
            log_warn "Ошибка сети (curl exit code $curl_rc), повторная попытка $attempt/$((max_attempts - 1))"
            notify_show "Аудит: повтор запроса" "curl exit code $curl_rc, попытка $attempt/$((max_attempts - 1))"
            sleep 1
            attempt=$((attempt + 1))
        else
            break
        fi
    done

    CURL_EXIT_CODE="$curl_rc"
    if (( curl_rc != 0 )); then
        log_error "Ошибка сети при запросе $(sanitize_url "$URL"): curl exit code $curl_rc, stderr: $(tr '\n' ' ' <"$err_file" 2>/dev/null | head -c 300)"
    fi

    parse_curl_stats "$stats_file"
}

parse_curl_stats() {                  # разбор строки -w curl
    local stats_file="$1"
    local line=""
    local t_namelookup="" t_connect="" t_appconnect="" t_pretransfer=""
    local t_starttransfer="" t_total="" sz="" code="" nredir="" ueff="" ctype=""

    line="$(awk '/@@CURL_STATS@@/{getline; print; exit}' "$stats_file" 2>/dev/null || true)"
    if [[ -n "$line" ]]; then
        IFS=$'\t' read -r t_namelookup t_connect t_appconnect t_pretransfer \
            t_starttransfer t_total sz code nredir ueff ctype <<<"$line"
    fi

    DNS_RESOLUTION="$(round3 "$t_namelookup")"
    TCP_CONNECTION="$(timing_diff "$t_connect" "$t_namelookup")"
    if [[ "$SCHEME" == "https" ]]; then
        TLS_HANDSHAKE="$(timing_diff "$t_appconnect" "$t_connect")"
    else
        TLS_HANDSHAKE=0.000
    fi
    TTFB="$(timing_diff "$t_starttransfer" "$t_pretransfer")"
    TOTAL="$(round3 "$t_total")"
    SIZE_DOWNLOAD="${sz:-0}"
    [[ "$SIZE_DOWNLOAD" =~ ^[0-9]+$ ]] || SIZE_DOWNLOAD=0
    HTTP_CODE="${code:-0}"
    [[ "$HTTP_CODE" =~ ^[0-9]+$ ]] || HTTP_CODE=0
    NUM_REDIRECTS="${nredir:-0}"
    [[ "$NUM_REDIRECTS" =~ ^[0-9]+$ ]] || NUM_REDIRECTS=0
    FINAL_URL="${ueff:-$URL}"
    [[ -n "$FINAL_URL" ]] || FINAL_URL="$URL"
    CONTENT_TYPE="${ctype:-}"
    if (( NUM_REDIRECTS > 0 )); then
        FOLLOWED=true
    else
        FOLLOWED=false
    fi
}

# ===========================================================================
# Анализ заголовков и содержимого
# ===========================================================================

get_header() {                        # get_header <блок заголовков> <Имя>
    local block="$1" name="$2"
    printf '%s\n' "$block" | grep -im1 "^${name}:" \
        | sed -E "s/^${name}:[[:space:]]*//I" \
        | tr -d '\r' \
        | sed -E 's/[[:space:]]+$//'
}

parse_headers_file() {
    local hf="$1"
    local block="" code="" ce=""

    # Последний блок заголовков (важно при --follow-redirects: он относится
    # к финальному ответу).
    block="$(tr -d '\r' <"$hf" | awk 'BEGIN{RS=""}{last=$0} END{print last}' 2>/dev/null || true)"
    if [[ -z "$block" ]]; then
        log_warn "Файл заголовков пуст — анализ заголовков пропущен"
        return 0
    fi

    code="$(printf '%s\n' "$block" | head -n1 | grep -oE '[0-9]{3}' | head -n1 || true)"
    HEADERS_HTTP_CODE="${code:-}"

    CONTENT_TYPE="$(get_header "$block" 'Content-Type')"
    SERVER="$(get_header "$block" 'Server')"
    HSTS="$(get_header "$block" 'Strict-Transport-Security')"
    CONTENT_LENGTH="$(get_header "$block" 'Content-Length')"
    [[ "$CONTENT_LENGTH" =~ ^[0-9]+$ ]] || CONTENT_LENGTH=""

    # Атрибуты Strict-Transport-Security
    if [[ -n "$HSTS" ]]; then
        HSTS_MAX_AGE="$(printf '%s' "$HSTS" | grep -oiE 'max-age=[0-9]+' | head -n1 | cut -d= -f2 || true)"
        if printf '%s' "$HSTS" | grep -qiE '(^|;)[[:space:]]*includeSubDomains'; then
            HSTS_INCLUDE_SUBDOMAINS=true
        fi
        if printf '%s' "$HSTS" | grep -qiE '(^|;)[[:space:]]*preload'; then
            HSTS_PRELOAD=true
        fi
    fi

    # Сжатие тела ответа (--compressed; size_download относится к переданному телу)
    ce="$(get_header "$block" 'Content-Encoding')"
    if [[ -n "$ce" && "$ce" != "identity" ]]; then
        COMPRESSED=true
    fi
}

decode_html_entities() {              # декодирование HTML-сущностей
    local s="$1"
    if has_cmd python3; then
        if printf '%s' "$s" | python3 -c 'import sys, html
sys.stdout.write(html.unescape(sys.stdin.read()))' 2>/dev/null; then
            return 0
        fi
    fi
    # Упрощённый fallback без python3
    s="${s//&amp;/\&}"
    s="${s//&lt;/<}"
    s="${s//&gt;/>}"
    s="${s//&quot;/\"}"
    s="${s//&#39;/\'}"
    s="${s//&apos;/\'}"
    s="${s//&nbsp;/ }"
    printf '%s' "$s"
}

extract_title() {                     # <title> из HTML (null при отсутствии)
    local html_file="$1"
    local joined="" raw=""
    joined="$(tr '\n\r' ' ' <"$html_file" 2>/dev/null || true)"
    raw="$(printf '%s' "$joined" | grep -oiP '<title[^>]*>.*?</title>' 2>/dev/null | head -n1)"
    if [[ -z "$raw" ]]; then
        # fallback без PCRE (grep -E)
        raw="$(printf '%s' "$joined" | grep -oiE '<title[^>]*>[^<]*</title>' | head -n1 || true)"
    fi
    if [[ -z "$raw" ]]; then
        TITLE=""
        return 0
    fi
    raw="$(printf '%s' "$raw" | sed -E 's#^<title[^>]*>##I; s#</title>.*$##I')"
    raw="$(printf '%s' "$raw" | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//')"
    TITLE="$(decode_html_entities "$raw")"
}

analyze_headers_and_content() {
    if [[ -f "$HEADERS_FILE" && -s "$HEADERS_FILE" ]]; then
        parse_headers_file "$HEADERS_FILE"
    else
        log_warn "Заголовки не получены (файл пуст или отсутствует)"
    fi

    if [[ -f "$HTML_FILE" && -s "$HTML_FILE" ]]; then
        extract_title "$HTML_FILE"
        SIZE_FILE="$(file_size "$HTML_FILE")"
    else
        SIZE_FILE=0
    fi

    # HTTP-код: предпочтительно от curl; иначе из файла заголовков
    if [[ -z "$HTTP_CODE" || "$HTTP_CODE" == "0" ]] && [[ -n "$HEADERS_HTTP_CODE" ]]; then
        HTTP_CODE="$HEADERS_HTTP_CODE"
    fi
}

# ===========================================================================
# Генерация JSON-отчёта
# ===========================================================================

json_escape() {                       # экранирование строки для JSON
    local s="$1"
    if has_cmd python3; then
        printf '%s' "$s" | python3 -c 'import json, sys
sys.stdout.write(json.dumps(sys.stdin.read()))' 2>/dev/null && return 0
    fi
    # fallback без python3 (без полного \uXXXX для управляющих символов)
    s="${s//\\/\\\\}"
    s="${s//\"/\\\"}"
    s="${s//$'\t'/\\t}"
    s="${s//$'\n'/\\n}"
    s="${s//$'\r'/\\r}"
    s="$(printf '%s' "$s" | sed -E 's/[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]/\uFFFD/g')"
    printf '"%s"' "$s"
}

json_null_or_str() {                  # "строка" либо null
    if [[ -z "$1" ]]; then
        printf 'null'
    else
        json_escape "$1"
    fi
}

artifact_path_or_null() {             # путь в file_paths либо null (если не сохраняем)
    if (( PERSIST_ARTIFACTS )) && [[ -n "$1" ]]; then
        json_escape "$1"
    else
        printf 'null'
    fi
}

build_json_jq() {                     # генерация через jq (--arg/--argjson)
    local has_server=0 has_hsts=0 has_title=0
    local has_html=0 has_headers=0 has_report=0 has_gist=0
    local hsts_age=0 clen=0

    [[ -n "$SERVER" ]] && has_server=1
    [[ -n "$HSTS" ]] && has_hsts=1
    [[ -n "$TITLE" ]] && has_title=1
    if (( PERSIST_ARTIFACTS )) && [[ -n "$HTML_FILE" ]]; then has_html=1; fi
    if (( PERSIST_ARTIFACTS )) && [[ -n "$HEADERS_FILE" ]]; then has_headers=1; fi
    [[ -n "$REPORT_FILE" ]] && has_report=1
    [[ -n "$GIST_URL" ]] && has_gist=1
    [[ "$HSTS_MAX_AGE" =~ ^[0-9]+$ ]] && hsts_age="$HSTS_MAX_AGE"
    [[ "$CONTENT_LENGTH" =~ ^[0-9]+$ ]] && clen="$CONTENT_LENGTH"

    REPORT_JSON="$(jq -n \
        --arg url "$URL" \
        --arg timestamp "$TIMESTAMP" \
        --argjson http_code "${HTTP_CODE:-0}" \
        --argjson dns "$DNS_RESOLUTION" \
        --argjson tcp "$TCP_CONNECTION" \
        --argjson tls "$TLS_HANDSHAKE" \
        --argjson ttfb "$TTFB" \
        --argjson total "$TOTAL" \
        --argjson size_download "${SIZE_DOWNLOAD:-0}" \
        --argjson size_file "${SIZE_FILE:-0}" \
        --arg content_type "$CONTENT_TYPE" \
        --arg server "$SERVER" \
        --argjson has_server "$has_server" \
        --arg hsts "$HSTS" \
        --argjson has_hsts "$has_hsts" \
        --argjson hsts_age "$hsts_age" \
        --argjson hsts_inc "$HSTS_INCLUDE_SUBDOMAINS" \
        --argjson hsts_pre "$HSTS_PRELOAD" \
        --argjson clen "$clen" \
        --arg title "$TITLE" \
        --argjson has_title "$has_title" \
        --argjson compressed "$COMPRESSED" \
        --argjson followed "$FOLLOWED" \
        --argjson nredirs "${NUM_REDIRECTS:-0}" \
        --arg final_url "$FINAL_URL" \
        --arg html_path "$HTML_FILE" \
        --argjson has_html "$has_html" \
        --arg headers_path "$HEADERS_FILE" \
        --argjson has_headers "$has_headers" \
        --arg report_path "$REPORT_FILE" \
        --argjson has_report "$has_report" \
        --arg gist_url "$GIST_URL" \
        --argjson has_gist "$has_gist" \
        --arg status "$STATUS" \
        --arg error "$ERROR_MSG" \
        '{
            url: $url,
            timestamp: $timestamp,
            http_code: $http_code,
            timings: {
                dns_resolution: $dns,
                tcp_connection: $tcp,
                tls_handshake: $tls,
                ttfb: $ttfb,
                total: $total
            },
            headers: {
                content_type: (if $content_type == "" then null else $content_type end),
                server: (if $has_server == 1 then $server else null end),
                strict_transport_security: (if $has_hsts == 1 then $hsts else null end),
                hsts: (if $has_hsts == 1 then {
                    max_age: $hsts_age,
                    include_subdomains: $hsts_inc,
                    preload: $hsts_pre
                } else null end),
                content_length: (if $clen == 0 then null else $clen end)
            },
            content: {
                title: (if $has_title == 1 then $title else null end),
                size_bytes: $size_file,
                size_transferred: $size_download,
                compressed: $compressed
            },
            redirects: {
                followed: $followed,
                count: $nredirs,
                final_url: $final_url
            },
            file_paths: {
                html: (if $has_html == 1 then $html_path else null end),
                headers: (if $has_headers == 1 then $headers_path else null end),
                report: (if $has_report == 1 then $report_path else null end)
            },
            gist_url: (if $has_gist == 1 then $gist_url else null end),
            status: $status,
            error: (if $error == "" then null else $error end)
        }')"
}

build_json_manual() {                 # ручная генерация JSON (без jq)
    local js=""
    js="{"
    js+="\"url\": $(json_escape "$URL"),"
    js+="\"timestamp\": $(json_escape "$TIMESTAMP"),"
    js+="\"http_code\": ${HTTP_CODE:-0},"
    js+="\"timings\": {"
    js+="\"dns_resolution\": ${DNS_RESOLUTION:-0}, \"tcp_connection\": ${TCP_CONNECTION:-0},"
    js+="\"tls_handshake\": ${TLS_HANDSHAKE:-0}, \"ttfb\": ${TTFB:-0}, \"total\": ${TOTAL:-0}"
    js+="},"
    js+="\"headers\": {"
    js+="\"content_type\": $(json_null_or_str "$CONTENT_TYPE"),"
    js+="\"server\": $(json_null_or_str "$SERVER"),"
    js+="\"strict_transport_security\": $(json_null_or_str "$HSTS"),"
    js+="\"content_length\": $(json_null_or_str "$CONTENT_LENGTH")"
    js+="},"
    js+="\"content\": {"
    js+="\"title\": $(json_null_or_str "$TITLE"),"
    js+="\"size_bytes\": ${SIZE_FILE:-0},"
    js+="\"size_transferred\": ${SIZE_DOWNLOAD:-0},"
    js+="\"compressed\": $COMPRESSED"
    js+="},"
    js+="\"redirects\": {"
    js+="\"followed\": $FOLLOWED, \"count\": ${NUM_REDIRECTS:-0},"
    js+="\"final_url\": $(json_escape "${FINAL_URL:-$URL}")"
    js+="},"
    js+="\"file_paths\": {"
    js+="\"html\": $(artifact_path_or_null "$HTML_FILE"),"
    js+="\"headers\": $(artifact_path_or_null "$HEADERS_FILE"),"
    js+="\"report\": $(json_null_or_str "$REPORT_FILE")"
    js+="},"
    js+="\"gist_url\": $(json_null_or_str "$GIST_URL"),"
    js+="\"status\": $(json_escape "${STATUS:-error}"),"
    js+="\"error\": $(json_null_or_str "$ERROR_MSG")"
    js+="}"
    REPORT_JSON="$js"

    # Валидация сгенерированного JSON (если python3 доступен)
    if has_cmd python3; then
        if ! printf '%s' "$REPORT_JSON" | python3 -m json.tool >/dev/null 2>&1; then
            log_error "Сгенерированный вручную JSON не прошёл валидацию"
            REPORT_JSON=""
            return 1
        fi
    fi
    return 0
}

build_report() {
    TIMESTAMP="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    if has_cmd jq; then
        build_json_jq
    else
        build_json_manual || return 1
    fi
    if [[ -z "$REPORT_JSON" ]]; then
        log_error "Не удалось сгенерировать JSON-отчёт"
        return 1
    fi
    return 0
}

save_report() {
    if [[ -z "$REPORT_FILE" ]]; then
        return 0
    fi
    if ! printf '%s\n' "$REPORT_JSON" >"$REPORT_FILE" 2>/dev/null; then
        log_error "Не удалось записать отчёт в $REPORT_FILE"
        return 1
    fi
    chmod 600 "$REPORT_FILE" 2>/dev/null || true
    return 0
}

# ===========================================================================
# GitHub Gist
# ===========================================================================

build_gist_request() {                # формирует JSON тела запроса
    local gist_json="$1" html_file="$2" fname="$3" desc="$4"
    local pub
    if (( GIST_PUBLIC )); then pub=true; else pub=false; fi

    if has_cmd jq; then
        if jq -n --arg desc "$desc" --argjson pub "$pub" --arg fname "$fname" \
            --rawfile content "$html_file" \
            '{description: $desc, public: $pub, files: {($fname): {content: $content}}}' \
            >"$gist_json" 2>/dev/null; then
            return 0
        fi
        log_warn "jq --rawfile недоступен (старая версия jq?) — пробуем python3"
    fi
    if has_cmd python3; then
        python3 - "$html_file" "$fname" "$desc" "$pub" >"$gist_json" <<'PYEOF'
import json, sys
html_file, fname, desc, pub = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
with open(html_file, "r", encoding="utf-8", errors="replace") as f:
    content = f.read()
payload = {
    "description": desc,
    "public": pub == "true",
    "files": {fname: {"content": content}},
}
json.dump(payload, sys.stdout)
PYEOF
        return $?
    fi
    log_error "Для загрузки на Gist требуется jq или python3"
    return 1
}

upload_gist() {                       # код возврата: 0 — успех, 5 — ошибка, 9 — пропуск
    local gist_json="" resp="" code="" fname="" desc="" api_msg="" size="" grc=0

    if [[ -z "$GITHUB_TOKEN" ]]; then
        if (( GUI_MODE )); then
            log_info "GITHUB_TOKEN не задан — запрашиваю токен через диалог"
            GITHUB_TOKEN="$(ask_token)"
            if [[ -z "$GITHUB_TOKEN" ]]; then
                log_info "Ввод токена отменён — загрузка на Gist пропущена"
                return 9
            fi
        else
            log_error "GITHUB_TOKEN не задан — загрузка на Gist невозможна"
            printf 'Ошибка: загрузка на Gist требует GITHUB_TOKEN (переменная окружения)\n' >&2
            return 5
        fi
    fi

    if [[ -z "$HTML_FILE" || ! -f "$HTML_FILE" || ! -s "$HTML_FILE" ]]; then
        log_error "HTML-копия страницы недоступна или пуста — загрузка на Gist невозможна"
        printf 'Ошибка: HTML-файл недоступен или пуст для загрузки на Gist\n' >&2
        return 5
    fi

    size="$(file_size "$HTML_FILE")"
    if (( size > GIST_LIMIT_BYTES )); then
        log_error "Размер HTML-копии ($size байт) превышает лимит GitHub Gist (10 МБ)"
        printf 'Ошибка: размер файла %s байт превышает лимит GitHub Gist (10 МБ)\n' "$size" >&2
        return 5
    fi

    fname="$(basename "$HTML_FILE")"
    desc="Аудит веб-ресурса $URL (analyzer.sh $SCRIPT_VERSION)"
    gist_json="$WORK_TMP/gist_request.json"
    resp="$WORK_TMP/gist_response.json"

    build_gist_request "$gist_json" "$HTML_FILE" "$fname" "$desc" || return 5

    code="$(curl --silent --show-error --connect-timeout 5 --max-time 15 \
        -o "$resp" -w '%{http_code}' \
        -H "Authorization: Bearer $GITHUB_TOKEN" \
        -H "Accept: application/vnd.github+json" \
        -H "Content-Type: application/json" \
        --data-binary "@$gist_json" \
        --url "https://api.github.com/gists")"
    grc=$?
    if (( grc != 0 )); then
        log_error "Ошибка сети при обращении к GitHub API: curl exit code $grc"
        return 5
    fi

    if [[ "$code" == "201" ]]; then
        if has_cmd jq; then
            GIST_URL="$(jq -r '.html_url // empty' "$resp" 2>/dev/null)"
        elif has_cmd python3; then
            GIST_URL="$(python3 -c 'import json, sys
try:
    print(json.load(sys.stdin).get("html_url", ""))
except Exception:
    pass' <"$resp" 2>/dev/null)"
        fi
        if [[ -z "$GIST_URL" ]]; then
            log_error "Ответ GitHub API не содержит html_url"
            return 5
        fi
        log_info "Gist создан: $GIST_URL"
        return 0
    fi

    # Ошибка GitHub API (401, 403, 422 и др.)
    if has_cmd jq; then
        api_msg="$(jq -r '.message // empty' "$resp" 2>/dev/null)"
    fi
    if [[ -z "$api_msg" && -f "$resp" ]]; then
        api_msg="$(head -c 300 "$resp" | tr '\n' ' ')"
    fi
    if [[ "$code" == "401" || "$code" == "403" ]]; then
        log_error "GitHub API: ошибка авторизации (HTTP $code): $api_msg"
        printf 'Ошибка: GitHub API отклонил запрос (HTTP %s). Проверьте токен и право gist.\n' "$code" >&2
    else
        log_error "GitHub API: ошибка (HTTP $code): $api_msg"
        printf 'Ошибка: GitHub API вернул HTTP %s: %s\n' "$code" "$api_msg" >&2
    fi
    return 5
}

# ===========================================================================
# Интерактивный (GUI) режим
# ===========================================================================

gui_input_url() {                     # termux-dialog text; 1 — отмена
    local out="" text="" code=""
    if ! out="$(termux-dialog text -t 'Введите URL для аудита' 2>/dev/null)"; then
        log_info "Диалог ввода URL отменён пользователем"
        return 1
    fi
    if has_cmd jq; then
        code="$(printf '%s' "$out" | jq -r '.code // 1' 2>/dev/null)"
        text="$(printf '%s' "$out" | jq -r '.text // empty' 2>/dev/null)"
        if [[ "$code" != "0" ]]; then
            log_info "Диалог ввода URL отменён пользователем"
            return 1
        fi
    elif has_cmd python3; then
        text="$(printf '%s' "$out" | python3 -c 'import json, sys
try:
    d = json.load(sys.stdin)
    sys.stdout.write(d.get("text", "") if d.get("code") == 0 else "")
except Exception:
    pass' 2>/dev/null)"
    else
        text="$(printf '%s' "$out" | grep -oE '"text"[[:space:]]*:[[:space:]]*"[^"]*"' | head -n1 | cut -d'"' -f4)"
    fi
    if [[ -z "$text" ]]; then
        log_info "Диалог ввода URL отменён (пустой ввод)"
        return 1
    fi
    printf '%s' "$text"
}

gui_ask_gist() {                      # termux-dialog confirm
    local out="" text=""
    if ! out="$(termux-dialog confirm -t 'Загрузить на Gist?' -i 'Загрузить HTML-копию страницы на GitHub Gist?' 2>/dev/null)"; then
        return 1
    fi
    if has_cmd jq; then
        text="$(printf '%s' "$out" | jq -r '.text // empty' 2>/dev/null)"
    elif has_cmd python3; then
        text="$(printf '%s' "$out" | python3 -c 'import json, sys
try:
    sys.stdout.write(json.load(sys.stdin).get("text", ""))
except Exception:
    pass' 2>/dev/null)"
    fi
    case "${text,,}" in
        yes|да|true|1) return 0 ;;
        *) return 1 ;;
    esac
}

gui_ask_token() {                     # termux-dialog password
    local out="" text=""
    if ! out="$(termux-dialog password -t 'GitHub токен' -i 'Вставьте Personal Access Token (право gist)' 2>/dev/null)"; then
        return 1
    fi
    if has_cmd jq; then
        text="$(printf '%s' "$out" | jq -r '.text // empty' 2>/dev/null)"
    elif has_cmd python3; then
        text="$(printf '%s' "$out" | python3 -c 'import json, sys
try:
    sys.stdout.write(json.load(sys.stdin).get("text", ""))
except Exception:
    pass' 2>/dev/null)"
    fi
    printf '%s' "$text"
}

text_ask_gist() {                     # упрощённый текстовый режим
    local ans=""
    read -r -p "Загрузить на Gist? [y/N] " ans || return 1
    case "${ans,,}" in
        y|yes|д|да) return 0 ;;
        *) return 1 ;;
    esac
}

text_ask_token() {
    local tok=""
    read -s -r -p "Введите GitHub Personal Access Token: " tok || return 1
    printf '\n' >&2
    printf '%s' "$tok"
}

ask_gist_confirm() {
    if has_cmd termux-dialog; then
        gui_ask_gist
    else
        text_ask_gist
    fi
}

ask_token() {
    if has_cmd termux-dialog; then
        gui_ask_token
    else
        text_ask_token
    fi
}

get_url_interactive() {               # ввод URL в GUI-режиме
    if has_cmd termux-dialog; then
        URL="$(gui_input_url)" || return 1
    else
        log_warn "termux-dialog не найден — переход в упрощённый текстовый режим"
        if ! read -r -p "Введите URL для аудита: " URL; then
            log_error "Не удалось получить URL из stdin (stdin закрыт?)"
            printf 'Ошибка: не указан URL (аргументом) и нет интерактивного ввода\n' >&2
            exit 1
        fi
    fi
    return 0
}

# ===========================================================================
# Вывод
# ===========================================================================

print_summary() {
    printf '=== Результат аудита ===\n'
    printf 'URL: %s\n' "$URL"
    printf 'HTTP-код: %s\n' "${HTTP_CODE:-—}"
    printf 'Время: DNS %s c | TCP %s c | TLS %s c\n' "$DNS_RESOLUTION" "$TCP_CONNECTION" "$TLS_HANDSHAKE"
    printf 'TTFB: %s c | Всего: %s c\n' "$TTFB" "$TOTAL"
    printf 'Размер тела: %s байт\n' "$SIZE_FILE"
    printf 'Заголовок: %s\n' "${TITLE:-—}"
    [[ -n "$GIST_URL" ]] && printf 'Gist: %s\n' "$GIST_URL"
    printf 'Отчёт: %s\n' "${REPORT_FILE:-—}"
    if [[ -n "$ERROR_MSG" ]]; then
        printf 'Ошибка: %s\n' "$ERROR_MSG"
    fi
    printf '=======================\n'
}

notify_result() {
    if (( ! USE_NOTIFICATION )); then
        return 0
    fi
    if (( EXIT_CODE == 0 )); then
        notify_show "Аудит завершён" \
            "HTTP ${HTTP_CODE:-?} • TTFB ${TTFB} c • всего ${TOTAL} c${GIST_URL:+ • Gist: $GIST_URL}"
    else
        notify_show "Аудит: ошибка" "${ERROR_MSG:-Ошибка выполнения (код $EXIT_CODE)}"
    fi
}

usage() {
    cat <<EOF
$SCRIPT_NAME $SCRIPT_VERSION — интерактивный аудит веб-ресурсов (Termux/Android)

Использование:
  $SCRIPT_NAME [URL] [опции]
  $SCRIPT_NAME --url <URL> [опции]

Режимы:
  Без аргумента URL запускается GUI-режим: URL запрашивается через
  termux-dialog (или read, если termux-api не установлен).
  С аргументом URL — headless/CLI-режим для Tasker, cron и других скриптов.

Опции:
  -u, --url <url>          URL для анализа (или позиционный аргумент)
  -t, --timeout <сек>      общий таймаут запроса (по умолчанию 30)
      --connect-timeout <сек>
                           таймаут установления соединения (по умолчанию 10)
  -o, --output-dir <путь>  каталог для сохранения файлов (по умолчанию текущий)
      --json-only          вывести только JSON-отчёт в stdout (без сообщений)
      --no-notification    отключить системные уведомления Termux
      --gist               автоматически загрузить HTML-копию на GitHub Gist
                           (требуется GITHUB_TOKEN)
      --gist-public        создать публичный Gist (по умолчанию secret)
      --max-size <байт>    максимальный размер загружаемого тела
                           (по умолчанию 10 МБ; 0 — без ограничения)
      --follow-redirects   следовать HTTP-редиректам (до 5)
  -h, --help               показать эту справку
      --version            показать версию скрипта

Коды возврата:
  0 — успех; 1 — общая ошибка; 2 — нет обязательных зависимостей;
  3 — невалидный URL/запрещённая схема; 4 — сетевая ошибка/таймаут;
  5 — ошибка GitHub API; 6 — ошибка генерации/сохранения отчёта.

Примеры:
  $SCRIPT_NAME https://example.com
  $SCRIPT_NAME --url example.com -o ~/audit --follow-redirects
  GITHUB_TOKEN=ghp_xxx $SCRIPT_NAME example.com --gist --json-only
EOF
}

version() {
    printf '%s %s\n' "$SCRIPT_NAME" "$SCRIPT_VERSION"
    printf 'Интерактивный аудит веб-ресурсов для Termux (Bash).\n'
}

# ===========================================================================
# Разбор аргументов командной строки
# ===========================================================================

parse_args() {
    while (( $# > 0 )); do
        case "$1" in
            -h|--help)
                usage
                exit 0
                ;;
            --version)
                version
                exit 0
                ;;
            -u|--url)
                shift
                if (( $# == 0 )); then die 1 "--url требует значение"; fi
                URL="$1"
                ;;
            -t|--timeout)
                shift
                if (( $# == 0 )); then die 1 "--timeout требует значение"; fi
                if [[ ! "$1" =~ ^[0-9]+$ ]]; then die 1 "--timeout ожидает целое число, получено: '$1'"; fi
                TIMEOUT="$1"
                ;;
            --connect-timeout)
                shift
                if (( $# == 0 )); then die 1 "--connect-timeout требует значение"; fi
                if [[ ! "$1" =~ ^[0-9]+$ ]]; then die 1 "--connect-timeout ожидает целое число, получено: '$1'"; fi
                CONNECT_TIMEOUT="$1"
                ;;
            -o|--output-dir)
                shift
                if (( $# == 0 )); then die 1 "--output-dir требует значение"; fi
                OUTPUT_DIR="$1"
                OUTPUT_DIR_SET=1
                ;;
            --json-only)
                JSON_ONLY=1
                ;;
            --no-notification)
                USE_NOTIFICATION=0
                ;;
            --gist)
                GIST_UPLOAD=1
                ;;
            --gist-public)
                GIST_PUBLIC=1
                ;;
            --max-size)
                shift
                if (( $# == 0 )); then die 1 "--max-size требует значение"; fi
                if [[ ! "$1" =~ ^[0-9]+$ ]]; then die 1 "--max-size ожидает целое число, получено: '$1'"; fi
                MAX_SIZE="$1"
                ;;
            --follow-redirects)
                FOLLOW_REDIRECTS=1
                ;;
            --)
                shift
                if (( $# >= 1 )) && [[ -z "$URL" ]]; then
                    URL="$1"
                fi
                break
                ;;
            -*)
                die 1 "Неизвестная опция: '$1' (используйте --help)"
                ;;
            *)
                if [[ -z "$URL" ]]; then
                    URL="$1"
                else
                    die 1 "URL задан более одного раза"
                fi
                ;;
        esac
        shift
    done
}

# ===========================================================================
# Подготовка окружения
# ===========================================================================

setup_output_dir() {
    if (( OUTPUT_DIR_SET )) && [[ "$OUTPUT_DIR" != "$PWD" ]]; then
        if ! mkdir -p "$OUTPUT_DIR" 2>/dev/null; then
            printf 'Ошибка: не удалось создать каталог: %s\n' "$OUTPUT_DIR" >&2
            exit 1
        fi
    fi
    # Абсолютный путь (для корректных file_paths в JSON)
    OUTPUT_DIR="$(cd "$OUTPUT_DIR" 2>/dev/null && pwd || printf '%s' "$OUTPUT_DIR")"
}

init_log() {
    LOG_FILE="$OUTPUT_DIR/error.log"
    if ! touch "$LOG_FILE" 2>/dev/null; then
        LOG_FILE="$PWD/error.log"
        printf 'Внимание: не удалось писать журнал в %s, использую %s\n' "$OUTPUT_DIR/error.log" "$LOG_FILE" >&2
        touch "$LOG_FILE" 2>/dev/null || LOG_FILE="/dev/null"
    fi
}

prepare_artifact_paths() {
    local ts pid
    ts="$(date '+%Y%m%d_%H%M%S')"
    pid="$$"
    if (( JSON_ONLY )) && (( ! OUTPUT_DIR_SET )); then
        # --json-only без --output-dir: артефакты не сохраняются
        PERSIST_ARTIFACTS=0
        HTML_FILE="$WORK_TMP/page_${ts}_${pid}.html"
        HEADERS_FILE="$WORK_TMP/headers_${ts}_${pid}.txt"
        REPORT_FILE=""
    else
        PERSIST_ARTIFACTS=1
        HTML_FILE="$OUTPUT_DIR/page_${ts}_${pid}.html"
        HEADERS_FILE="$OUTPUT_DIR/headers_${ts}_${pid}.txt"
        REPORT_FILE="$OUTPUT_DIR/report_${ts}_${pid}.json"
    fi
}

# ===========================================================================
# Обработка сигналов и очистка
# ===========================================================================

# shellcheck disable=SC2329 # вызывается косвенно (trap EXIT)
cleanup() {
    notify_dismiss
    if [[ -n "$WORK_TMP" && -d "$WORK_TMP" ]]; then
        rm -rf "$WORK_TMP" 2>/dev/null || true
    fi
}

# shellcheck disable=SC2329 # вызывается косвенно (trap INT TERM)
interrupt_handler() {
    printf 'Прервано пользователем\n' >&2
    log_info "Прервано пользователем (SIGINT/SIGTERM)"
    cleanup
    exit 130
}

determine_status() {
    if (( CURL_EXIT_CODE == 63 )); then
        STATUS="error"
        ERROR_MSG="Превышен лимит размера ответа (--max-size ${MAX_SIZE} байт)"
        return 0
    fi
    if (( CURL_EXIT_CODE != 0 )); then
        STATUS="error"
        ERROR_MSG="${ERROR_MSG:-Сетевая ошибка (curl exit code $CURL_EXIT_CODE)}"
        return 0
    fi
    if [[ "$HTTP_CODE" =~ ^2[0-9][0-9]$ ]]; then
        STATUS="success"
    else
        STATUS="partial"
    fi
}

# ===========================================================================
# Главная функция
# ===========================================================================

main() {
    parse_args "$@"

    trap cleanup EXIT
    trap interrupt_handler INT TERM

    WORK_TMP="$(mktemp -d "${TMPDIR:-/tmp}/analyzer.XXXXXX" 2>/dev/null || mktemp -d)"
    if [[ -z "$WORK_TMP" || ! -d "$WORK_TMP" ]]; then
        printf 'Ошибка: не удалось создать временную директорию\n' >&2
        exit 1
    fi

    setup_output_dir
    init_log
    check_deps

    log_info "Запуск аудита (analyzer.sh $SCRIPT_VERSION, PID $$)"

    # --- Режим работы: GUI по умолчанию, если URL не передан ---
    if [[ -z "$URL" ]]; then
        GUI_MODE=1
        if ! get_url_interactive; then
            log_info "Аудит отменён пользователем"
            exit 0
        fi
    fi

    # --- Валидация и нормализация URL ---
    if ! normalize_and_validate_url "$URL"; then
        exit 3
    fi
    URL="$VALIDATED_URL"

    prepare_artifact_paths

    log_info "Начало аудита: $(sanitize_url "$URL")"
    notify_show "Аудит веб-ресурса" "Выполняется запрос к $(host_of "$URL")..."
    if (( ! JSON_ONLY )); then
        printf 'Выполняется запрос к %s...\n' "$(sanitize_url "$URL")" >&2
    fi

    run_audit
    if (( CURL_EXIT_CODE != 0 )); then
        EXIT_CODE=4
    fi

    # Права доступа: 600 для заголовков, 644 для HTML (раздел 2.9 ТЗ)
    chmod 600 "$HEADERS_FILE" 2>/dev/null || true
    chmod 644 "$HTML_FILE" 2>/dev/null || true

    analyze_headers_and_content
    determine_status

    log_info "Завершение аудита: HTTP ${HTTP_CODE:-0}, status=$STATUS"

    # --- Отчёт ---
    if ! build_report; then
        EXIT_CODE=6
    elif ! save_report; then
        EXIT_CODE=6
    fi

    if (( ! JSON_ONLY )); then
        print_summary
    fi

    # Уведомление об успехе/ошибке аудита (раздел 2.3 ТЗ, шаг 4)
    notify_result

    # --- Загрузка на Gist ---
    if (( GIST_UPLOAD )) || { (( GUI_MODE )) && [[ -s "$HTML_FILE" ]] && ask_gist_confirm; }; then
        upload_gist
        local grc=$?
        if (( grc == 0 )); then
            # Обновляем отчёт с gist_url
            if build_report; then
                save_report || EXIT_CODE=6
            else
                EXIT_CODE=6
            fi
            notify_show "Gist создан" "$GIST_URL"
        elif (( grc == 9 )); then
            log_info "Загрузка на Gist пропущена"
        else
            EXIT_CODE=5
        fi
    fi

    if (( JSON_ONLY )); then
        printf '%s\n' "$REPORT_JSON"
    fi

    log_info "Аудит завершён, exit code $EXIT_CODE"
    exit "$EXIT_CODE"
}

main "$@"
