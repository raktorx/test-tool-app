#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gui/server.py — веб-GUI для analyzer.sh (аудит веб-ресурсов в браузере).

Небольшой HTTP-сервер на стандартной библиотеке Python (без зависимостей):

  * GET  /            — одностраничный интерфейс (gui/static/index.html);
  * POST /api/audit   — запуск analyzer.sh в режиме --json-only и возврат
                        JSON-отчёта {report, exit_code, stderr, duration};
  * GET  /api/health  — проверка живости сервера и доступности analyzer.sh.

Запуск:
    python3 gui/server.py                 # 0.0.0.0:8000
    python3 gui/server.py --port 8080
    python3 gui/server.py --host 127.0.0.1 --port 9000

Особенности:
  * URL и опции передаются analyzer.sh только массивом аргументов —
    без shell=True (никаких инъекций);
  * отчёт читается из stdout (--json-only), артефакты на диск не пишутся;
  * одновременные аудиты ограничены семафором (по умолчанию 4);
  * таймаут субпроцесса = таймаут запроса + запас 15 с.
"""

import argparse
import json
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ANALYZER = ROOT / "analyzer.sh"
STATIC = Path(__file__).resolve().parent / "static"

MAX_CONCURRENT = 4
_SEMAPHORE = threading.BoundedSemaphore(MAX_CONCURRENT)

# Максимальные значения, которые принимает GUI (защита от злоупотреблений)
LIMITS = {
    "timeout": 120,
    "connect_timeout": 60,
    "max_size": 100 * 1024 * 1024,
}


def _int_in_range(value, default, lo, hi):
    try:
        v = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(v, hi))


def build_args(payload: dict) -> list:
    """Формирует argv для analyzer.sh из JSON-запроса GUI."""
    url = str(payload.get("url") or "").strip()
    if not url:
        raise ValueError("Не указан URL")
    if re.search(r"[\x00-\x1f\x7f]", url):
        raise ValueError("URL содержит управляющие символы")

    timeout = _int_in_range(payload.get("timeout"), 30, 1, LIMITS["timeout"])
    connect_timeout = _int_in_range(payload.get("connect_timeout"), 10, 1,
                                    LIMITS["connect_timeout"])
    max_size = _int_in_range(payload.get("max_size"),
                             10 * 1024 * 1024, 0, LIMITS["max_size"])

    args = [
        "bash", str(ANALYZER),
        "--url", url,
        "--json-only",
        "--no-notification",
        "--timeout", str(timeout),
        "--connect-timeout", str(connect_timeout),
        "--max-size", str(max_size),
    ]
    if payload.get("follow_redirects"):
        args.append("--follow-redirects")
    return args, timeout


def run_audit(payload: dict) -> dict:
    args, timeout = build_args(payload)
    started = time.monotonic()
    try:
        proc = subprocess.run(
            args,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=timeout + 15,
            env={"NO_COLOR": "1", "PATH": "/usr/local/bin:/usr/bin:/bin",
                 "HOME": str(Path.home()), "TMPDIR": "/tmp"},
        )
        exit_code = proc.returncode
        stdout, stderr = proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Аудит превысил таймаут выполнения",
                "exit_code": 124}

    report = None
    try:
        report = json.loads(stdout) if stdout.strip() else None
    except ValueError:
        pass

    return {
        "ok": exit_code == 0 and report is not None,
        "exit_code": exit_code,
        "report": report,
        "stderr": stderr[-4000:],
        "duration": round(time.monotonic() - started, 3),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "AnalyzerGUI/1.0"

    # -- helpers ------------------------------------------------------------

    def _send_json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, ctype: str) -> None:
        try:
            body = path.read_bytes()
        except OSError:
            self._send_json(404, {"error": "not found"})
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # компактный лог
        sys.stderr.write("[gui] %s %s\n" % (self.address_string(), fmt % args))

    # -- маршруты -------------------------------------------------------------

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._send_file(STATIC / "index.html", "text/html; charset=utf-8")
        elif path == "/api/health":
            self._send_json(200, {
                "ok": True,
                "analyzer": ANALYZER.exists(),
                "analyzer_path": str(ANALYZER),
            })
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path != "/api/audit":
            self._send_json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self._send_json(400, {"ok": False, "error": "Некорректный JSON"})
            return

        if not _SEMAPHORE.acquire(blocking=False):
            self._send_json(429, {
                "ok": False,
                "error": f"Слишком много одновременных аудитов "
                         f"(максимум {MAX_CONCURRENT})"})
            return
        try:
            result = run_audit(payload)
        except ValueError as e:
            self._send_json(400, {"ok": False, "error": str(e)})
            return
        finally:
            _SEMAPHORE.release()
        self._send_json(200, result)


def main() -> int:
    ap = argparse.ArgumentParser(description="Веб-GUI для analyzer.sh")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()

    if not ANALYZER.exists():
        print(f"FATAL: analyzer.sh не найден: {ANALYZER}", file=sys.stderr)
        return 1

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"GUI запущен: http://{args.host}:{args.port}/ "
          f"(analyzer: {ANALYZER})", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено")
    return 0


if __name__ == "__main__":
    sys.exit(main())
