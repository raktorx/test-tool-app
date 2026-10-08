#!/usr/bin/env python3
"""Локальный HTTP-сервер для тестов analyzer.sh.

Использование: python3 server.py [порт]
"""
import gzip
import http.server
import socketserver
import sys
import time

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 18765
BIG_BODY = b"x" * (11 * 1024 * 1024)  # 11 МБ — для теста --max-size

HTML_INDEX = (
    b"<!DOCTYPE html>\n"
    b"<html><head><title>Test Page</title></head>\n"
    b"<body><h1>Hello</h1></body></html>\n"
)


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # тишина в логах
        pass

    def send_response(self, code, message=None):
        # Не отправляем заголовок Server по умолчанию — добавляем его явно,
        # чтобы можно было тестировать ответы без Server.
        self.log_request(code)
        self.send_response_only(code, message)
        self.send_header("Date", self.date_time_string())

    def _send(self, code, body, extra_headers=None, ctype="text/html; charset=utf-8"):
        self.send_response(code)
        if ctype:
            self.send_header("Content-Type", ctype)
        for k, v in (extra_headers or []):
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        p = self.path.split("?", 1)[0]
        if p == "/":
            self._send(200, HTML_INDEX, [
                ("Server", "TestServer/1.0"),
                ("Strict-Transport-Security", "max-age=31536000; includeSubDomains; preload"),
            ])
        elif p == "/notitle":
            self._send(200, b"<html><body>no title here</body></html>",
                       [("Server", "TestServer/1.0")])
        elif p == "/noheaders":
            # Ответ без Server, без Content-Length, без Content-Encoding
            body = b"<html><head><title>Bare</title></head><body>x</body></html>"
            self.send_response(200)
            self.send_header("Server", "")
            self.send_header("Content-Type", "text/html")
            self.send_header("Connection", "close")
            self.close_connection = True
            self.end_headers()
            self.wfile.write(body)
        elif p == "/entities":
            body = (b"<html><head><title>A &amp; B &lt; C &gt; D &quot;Q&quot; &nbsp; end"
                    b"</title></head><body></body></html>")
            self._send(200, body)
        elif p == "/multiline":
            body = b"<html><head>\n<title>\nMulti\nline\n</title>\n</head><body></body></html>"
            self._send(200, body)
        elif p == "/utf8":
            body = "<html><head><title>Тестовая страница</title></head><body></body></html>".encode("utf-8")
            self._send(200, body)
        elif p == "/nocontenttype":
            body = b"<html><head><title>NoCT</title></head></html>"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif p == "/gzip":
            raw = b"<html><head><title>Gzipped</title></head><body>" + b"z" * 5000 + b"</body></html>"
            body = gzip.compress(raw)
            self._send(200, body, [("Content-Encoding", "gzip")],
                       ctype="text/html; charset=utf-8")
        elif p == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif p == "/redirect-chain":
            self.send_response(302)
            self.send_header("Location", "/redirect")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif p == "/redirect-external":
            # редирект на не-HTTP схему (проверка --proto-redir)
            self.send_response(302)
            self.send_header("Location", "file:///etc/passwd")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif p == "/404":
            self._send(404, b"<html><head><title>Not Found</title></head><body>missing</body></html>")
        elif p == "/500":
            self._send(500, b"<html><head><title>Server Error</title></head></html>")
        elif p == "/big":
            self._send(200, BIG_BODY, [("Server", "TestServer/1.0")],
                       ctype="application/octet-stream")
        elif p == "/slow":
            time.sleep(30)
            self._send(200, HTML_INDEX)
        else:
            self._send(404, b"<html><body>not found</body></html>")

    def do_HEAD(self):
        self.do_GET()


if __name__ == "__main__":
    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    with Server(("127.0.0.1", PORT), Handler) as httpd:
        print("listening on %d" % PORT, flush=True)
        httpd.serve_forever()
