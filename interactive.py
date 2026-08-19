#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
interactive.py — интерактивные элементы CLI для f2c_inventory.

  * choose()    — выбор из списка: стрелки ↑/↓, живой фильтр по подстроке,
                  Enter — выбрать, Esc — очистить фильтр / отмена;
  * ask_text()  — ввод строки с умолчанием и проверкой обязательности;
  * ask_yesno() — подтверждение да/нет;
  * confirm_payload() — показать JSON и спросить подтверждение.

Если stdin/stdout не терминал (cron, конвейеры, перенаправление) — все
элементы автоматически переключаются на простой нумерованный список и
обычный input(), поэтому скрипты продолжают работать.
"""

import json
import os
import sys

_ANSI_ENABLED = False


def is_tty() -> bool:
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except Exception:
        return False


def _enable_ansi() -> bool:
    global _ANSI_ENABLED
    if _ANSI_ENABLED:
        return True
    if os.name == "nt":
        # включить обработку ANSI-последовательностей в консоли Windows 10+
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
        except Exception:
            pass
        _ANSI_ENABLED = True
    else:
        term = (os.environ.get("TERM") or "").lower()
        _ANSI_ENABLED = bool(term) and term != "dumb"
    return _ANSI_ENABLED


def _read_key():
    """Читает одну клавишу: 'up'/'down'/символ/'\r'/'esc'. Только в TTY."""
    if os.name == "nt":
        import msvcrt
        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):
            return {"H": "up", "P": "down"}.get(msvcrt.getwch())
        return ch
    import select
    import termios
    import tty
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        ch = sys.stdin.read(1)
        if ch == "\x1b":
            ready, _, _ = select.select([sys.stdin], [], [], 0.05)
            if not ready:
                return "esc"
            seq = sys.stdin.read(2)
            return {"[A": "up", "[B": "down"}.get(seq, "esc")
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def choose(title, options, display=None, allow_search=True,
           search_hint="", page_size=10):
    """Интерактивный выбор одного элемента. Возвращает элемент или None.

    options — список произвольных объектов; display(o) -> строка.
    Интерактивный режим: ↑/↓ навигация, ввод текста — фильтр по подстроке,
    Enter — выбор, Esc — очистить фильтр (или отмена, если фильтр пуст).
    Неинтерактивный режим: нумерованный список, ввод номера (0 — отмена).
    """
    if not options:
        print("(вариантов нет)")
        return None
    display = display or (lambda o: str(o))
    if not is_tty() or not _enable_ansi():
        print(title)
        for i, o in enumerate(options, 1):
            print(f"  [{i}] {display(o)}")
        print("  [0] отмена")
        for _ in range(5):
            try:
                raw = input("> ").strip()
            except EOFError:
                return None
            if raw in ("", "0"):
                return None
            try:
                idx = int(raw) - 1
            except ValueError:
                continue
            if 0 <= idx < len(options):
                return options[idx]
        print("(слишком много неверных попыток — отмена)")
        return None

    visible = list(options)
    sel = 0
    query = ""
    printed = 0

    def render():
        nonlocal printed
        if printed:
            sys.stdout.write("\x1b[%dA\x1b[J" % printed)
        lines = [f"▶ {title}"]
        lines.append(search_hint or ("↑/↓ — выбор · текст — фильтр · "
                                     "Enter — выбрать · Esc — очистить/отмена"))
        if query:
            lines.append(f"  фильтр: {query}_")
        start = max(0, min(sel - page_size // 2, len(visible) - page_size))
        end = min(start + page_size, len(visible))
        if start > 0:
            lines.append("  … (выше)")
        for i in range(start, end):
            mark = "▶" if i == sel else " "
            lines.append(f" {mark} {display(visible[i])}")
        if end < len(visible):
            lines.append("  … (ниже)")
        if not visible:
            lines.append("  (ничего не найдено — очистите фильтр: Esc)")
        sys.stdout.write("\n".join(lines) + "\n")
        printed = len(lines) + 1
        sys.stdout.flush()

    while True:
        if query:
            q = query.lower()
            visible = [o for o in options if q in display(o).lower()]
            if sel >= len(visible):
                sel = max(0, len(visible) - 1)
        else:
            visible = list(options)
        render()
        k = _read_key()
        if k == "up":
            if visible:
                sel = (sel - 1) % len(visible)
        elif k == "down":
            if visible:
                sel = (sel + 1) % len(visible)
        elif k in ("\r", "\n"):
            if visible:
                sys.stdout.write("\n")
                return visible[sel]
        elif k == "esc":
            if query:
                query = ""
                sel = 0
            else:
                sys.stdout.write("\n")
                return None
        elif k in ("\x7f", "\x08"):
            query = query[:-1]
            sel = 0
        elif isinstance(k, str) and k.isprintable():
            query += k
            sel = 0


def ask_text(prompt, default="", required=False, hidden=False):
    """Ввод строки. Возвращает значение или None (пустой ввод без default)."""
    if hidden:
        import getpass
        v = getpass.getpass(prompt + ": ")
        return v if v.strip() else None
    suffix = f" [по умолчанию: {default}]: " if default else ": "
    while True:
        try:
            v = input(prompt + suffix).strip()
        except EOFError:
            return default or None
        if not v and default:
            v = default
        if required and not v:
            print("  ← обязательное поле")
            continue
        return v if v else None


def ask_yesno(prompt, default=False):
    hint = "Y/n" if default else "y/N"
    try:
        v = input(f"{prompt} [{hint}] ").strip().lower()
    except EOFError:
        return default
    if not v:
        return default
    return v in ("y", "yes", "д", "да", "1")


def confirm_payload(title, payload):
    print(title)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return ask_yesno("Отправить запрос?")
