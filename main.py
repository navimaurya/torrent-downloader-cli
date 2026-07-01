#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Mint Torrent TUI
- Full-screen curses UI (arrow keys to move, Enter to select, Esc/Ctrl+C to quit)
- Choose magnet OR .torrent file via Home-rooted browser
- Choose destination directory (defaults to ~/Downloads/torrents)
- Live progress, mint-green theme; KB/s under 1 MB/s else MB/s
- Auto-installs dependencies (typer, python-libtorrent, windows-curses on Windows)

Tested with Python 3.12+. If libtorrent wheel isn't available for your OS/Python,
you may need system packages (e.g., Ubuntu: `sudo apt-get install python3-libtorrent`).
"""

from __future__ import annotations

import sys
import os
import time
import platform
import subprocess
import importlib
import ctypes
from pathlib import Path
from typing import List, Optional, Tuple


# ---------- Auto-install helpers ----------

def _pip_install(pkg: str) -> bool:
    """Install a package with pip; return True on success."""
    try:
        print(f"[setup] Installing {pkg} ...")
        # Prefer --upgrade to grab wheels that may have been published after Python updates
        cmd = [sys.executable, "-m", "pip", "install", "--upgrade", pkg]
        rc = subprocess.call(cmd)
        return rc == 0
    except Exception as e:
        print(f"[setup] Failed to run pip for {pkg}: {e}")
        return False


def ensure_module(import_name: str, pip_name: Optional[str] = None, only_on_windows: bool = False):
    """
    Try to import a module; if missing, pip-install it (optionally only on Windows).
    Returns the imported module or raises ImportError if it ultimately fails.
    """
    try:
        return importlib.import_module(import_name)
    except ImportError:
        if only_on_windows and platform.system() != "Windows":
            raise
        pkg = pip_name or import_name
        ok = _pip_install(pkg)
        if not ok:
            # As a last resort, try upgrading pip itself (sometimes needed on fresh 3.12+)
            _pip_install("pip")
            ok = _pip_install(pkg)
        if not ok:
            raise
        return importlib.import_module(import_name)


# Typer is required for the CLI
typer = ensure_module("typer")  # pip name is the same

# curses: built-in on Linux/macOS; on Windows we need windows-curses
try:
    curses = importlib.import_module("curses")
except ImportError:
    # Attempt Windows-specific shim
    ensure_module("curses", pip_name="windows-curses", only_on_windows=True)
    curses = importlib.import_module("curses")

# libtorrent (BitTorrent engine)
try:
    lt = importlib.import_module("libtorrent")
except ImportError:
    # Try installing python-libtorrent (the PyPI name for libtorrent bindings)
    try:
        ensure_module("libtorrent", pip_name="python-libtorrent")
        lt = importlib.import_module("libtorrent")
    except Exception as e:
        # Give a helpful message and exit cleanly
        msg = (
            "\nERROR: Could not import/install libtorrent.\n"
            "Try a system package:\n"
            "  • Ubuntu/Debian:  sudo apt-get install python3-libtorrent\n"
            "  • Arch Linux:     sudo pacman -S python-libtorrent-rasterbar\n"
            "  • macOS (Homebrew): brew install libtorrent-rasterbar  # then try pip again\n"
            "  • Windows: Use Python wheels from PyPI (python-libtorrent) if available for your version.\n"
        )
        print(msg)
        raise


# ---------- CLI app ----------

app = typer.Typer(add_completion=False, no_args_is_help=False)


# ---------- Utility ----------

def default_download_dir() -> Path:
    home = Path.home()
    base = (home / "Downloads") if (home / "Downloads").exists() else (
        home / "downloads" if (home / "downloads").exists() else home
    )
    target = base / "torrents"
    target.mkdir(parents=True, exist_ok=True)
    return target


def is_magnet(s: str) -> bool:
    return s.strip().lower().startswith("magnet:")


def format_rate(bps: float) -> str:
    if not bps:
        return "0.0 kB/s"
    kbps = bps / 1000.0
    if kbps >= 1000.0:
        return f"{kbps / 1000.0:.1f} MB/s"
    return f"{kbps:.1f} kB/s"


def make_session() -> lt.session:
    ses = lt.session()
    ses.listen_on(6881, 6891)
    ses.add_dht_router("router.bittorrent.com", 6881)
    ses.add_dht_router("router.utorrent.com", 6881)
    ses.add_dht_router("dht.transmissionbt.com", 6881)
    ses.start_dht()
    return ses


# ---------- Curses helpers (mint-green theme) ----------

COLOR_HEADER = 1       # black on green
COLOR_HILITE = 2       # black on green (selected row)
COLOR_MUTED = 3        # green on default
COLOR_WARN = 4         # black on yellow
COLOR_ERROR = 5        # black on red
COLOR_BAR = 6          # black on green (progress fill)

def init_colors():
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(COLOR_HEADER, curses.COLOR_BLACK, curses.COLOR_GREEN)
    curses.init_pair(COLOR_HILITE, curses.COLOR_BLACK, curses.COLOR_GREEN)
    curses.init_pair(COLOR_MUTED, curses.COLOR_GREEN, -1)
    curses.init_pair(COLOR_WARN, curses.COLOR_BLACK, curses.COLOR_YELLOW)
    curses.init_pair(COLOR_ERROR, curses.COLOR_BLACK, curses.COLOR_RED)
    curses.init_pair(COLOR_BAR, curses.COLOR_BLACK, curses.COLOR_GREEN)

def safe_addnstr(win, y: int, x: int, s: str, max_right_margin: int = 1):
    """
    Safely write a string, ensuring we don't hit the bottom-right cell.
    max_right_margin=1 leaves one column free to avoid addwstr ERR.
    """
    try:
        h, w = win.getmaxyx()
        if y < 0 or y >= h or x < 0 or x >= w:
            return
        # leave one column margin to avoid the cursed bottom-right cell
        max_len = max(0, (w - max_right_margin) - x)
        if max_len <= 0:
            return
        win.addnstr(y, x, s, max_len)
    except curses.error:
        pass

def draw_header(stdscr, text: str):
    h, w = stdscr.getmaxyx()
    stdscr.attron(curses.color_pair(COLOR_HEADER))
    # fill line without touching the last column
    safe_addnstr(stdscr, 0, 0, " " * (w - 1))
    # center text (and clip safely)
    start_x = max(0, (w - len(text)) // 2)
    safe_addnstr(stdscr, 0, start_x, text)
    stdscr.attroff(curses.color_pair(COLOR_HEADER))



def draw_footer(stdscr, text: str):
    h, w = stdscr.getmaxyx()
    stdscr.attron(curses.color_pair(COLOR_MUTED))
    # fill footer line safely
    safe_addnstr(stdscr, h - 1, 0, " " * (w - 1))
    # write footer text safely
    safe_addnstr(stdscr, h - 1, 1, text)
    stdscr.attroff(curses.color_pair(COLOR_MUTED))


def text_input(stdscr, prompt: str, initial: str = "") -> Optional[str]:
    curses.curs_set(1)
    h, w = stdscr.getmaxyx()
    buf = list(initial)
    pos = len(buf)
    while True:
        stdscr.clear()
        draw_header(stdscr, " Mint Torrent — Enter Magnet Link ")
        stdscr.addstr(2, 2, prompt[: w - 4])
        line = "".join(buf)
        stdscr.addstr(4, 2, line[: w - 4])
        draw_footer(stdscr, "Enter: confirm • Esc: cancel • Ctrl+C: quit")
        stdscr.move(4, 2 + min(pos, max(0, w - 4)))
        stdscr.refresh()
        ch = stdscr.getch()
        if ch in (3,):  # Ctrl+C
            raise KeyboardInterrupt
        if ch in (27,):  # Esc
            curses.curs_set(0)
            return None
        if ch in (curses.KEY_ENTER, 10, 13):
            curses.curs_set(0)
            return "".join(buf).strip()
        if ch in (curses.KEY_LEFT,):
            pos = max(0, pos - 1)
        elif ch in (curses.KEY_RIGHT,):
            pos = min(len(buf), pos + 1)
        elif ch in (curses.KEY_BACKSPACE, 127, 8):
            if pos > 0:
                pos -= 1
                buf.pop(pos)
        elif ch == curses.KEY_DC:  # Delete
            if pos < len(buf):
                buf.pop(pos)
        elif 0 <= ch <= 255 and chr(ch).isprintable():
            buf.insert(pos, chr(ch))
            pos += 1

def is_hidden_path(p: Path) -> bool:
    """Return True if path is hidden (.* on Unix/macOS, Hidden/System on Windows)."""
    try:
        # Unix/macOS: dotfiles
        if p.name.startswith("."):
            return True
        # Windows: FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM
        if sys.platform.startswith("win"):
            FILE_ATTRIBUTE_HIDDEN = 0x2
            FILE_ATTRIBUTE_SYSTEM = 0x4
            attrs = ctypes.windll.kernel32.GetFileAttributesW(str(p))
            if attrs == -1:
                return False
            return bool(attrs & (FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM))
    except Exception:
        pass
    return False

def list_dir(start: Path, allow_suffix: Optional[str]) -> List[Path]:
    """List non-hidden dirs (always) and optional non-hidden files with suffix."""
    try:
        entries = [e for e in start.iterdir() if not is_hidden_path(e)]
    except PermissionError:
        return []
    dirs = sorted([e for e in entries if e.is_dir()], key=lambda p: p.name.lower())
    files = []
    if allow_suffix:
        files = sorted(
            [e for e in entries if e.is_file() and e.suffix.lower() == allow_suffix],
            key=lambda p: p.name.lower()
        )
    return dirs + files

def file_browser(stdscr, start: Path, suffix: str = ".torrent") -> Optional[Path]:
    curses.curs_set(0)
    cwd = start
    idx = 0
    while True:
        stdscr.clear()
        draw_header(stdscr, " Mint Torrent — Select .torrent File ")
        h, w = stdscr.getmaxyx()
        y = 2
        stdscr.addstr(y, 2, f"📂 {cwd}"[: w - 4]); y += 1
        stdscr.addstr(y, 2, "↑/↓ move • Enter open/select • ← up • Esc cancel"); y += 2

        items: List[Tuple[str, Path]] = []
        if cwd.parent != cwd:
            items.append(("[..]  Go up", cwd.parent))
        entries = list_dir(cwd, allow_suffix=suffix)
        for p in entries:
            label = f"[DIR] {p.name}/" if p.is_dir() else f"[FILE] {p.name}"
            items.append((label, p))

        idx = max(0, min(idx, len(items) - 1))

        max_rows = h - y - 2
        top = max(0, idx - max_rows + 1) if len(items) > max_rows else 0
        for i in range(top, min(len(items), top + max_rows)):
            row = i - top
            label, p = items[i]
            if i == idx:
                stdscr.attron(curses.color_pair(COLOR_HILITE))
                stdscr.addstr(y + row, 2, label[: w - 4])
                stdscr.attroff(curses.color_pair(COLOR_HILITE))
            else:
                stdscr.addstr(y + row, 2, label[: w - 4])

        draw_footer(stdscr, "Esc: cancel • Ctrl+C: quit")
        stdscr.refresh()
        ch = stdscr.getch()
        if ch in (3,):  # Ctrl+C
            raise KeyboardInterrupt
        if ch in (27,):  # Esc
            return None
        if ch in (curses.KEY_UP,):
            idx = max(0, idx - 1)
        elif ch in (curses.KEY_DOWN,):
            idx = min(len(items) - 1, idx + 1) if items else 0
        elif ch in (curses.KEY_LEFT, curses.KEY_BACKSPACE, 127, 8):
            if cwd.parent != cwd:
                cwd = cwd.parent
                idx = 0
        elif ch in (curses.KEY_ENTER, 10, 13):
            if not items:
                continue
            label, p = items[idx]
            if p.is_dir():
                cwd = p
                idx = 0
            else:
                return p
        elif ch == curses.KEY_RESIZE:
            pass


def dir_browser(stdscr, start: Path) -> Optional[Path]:
    curses.curs_set(0)
    cwd = start
    idx = 0
    while True:
        stdscr.clear()
        draw_header(stdscr, " Mint Torrent — Select Download Folder ")
        h, w = stdscr.getmaxyx()
        y = 2
        stdscr.addstr(y, 2, f"📂 {cwd}"[: w - 4]); y += 1
        stdscr.addstr(y, 2, "↑/↓ move • Enter open/select • ← up • Esc cancel • Space = Use this folder"); y += 2

        items: List[Tuple[str, Path]] = []
        items.append(("✅  Use this folder", cwd))
        if cwd.parent != cwd:
            items.append(("[..]  Go up", cwd.parent))
        try:
            dirs = sorted(
                [d for d in cwd.iterdir() if d.is_dir() and not is_hidden_path(d)],
                key=lambda p: p.name.lower()
            )
        except PermissionError:
            dirs = []
        for d in dirs:
            items.append((f"[DIR] {d.name}/", d))

        idx = max(0, min(idx, len(items) - 1))

        max_rows = h - y - 2
        top = max(0, idx - max_rows + 1) if len(items) > max_rows else 0
        for i in range(top, min(len(items), top + max_rows)):
            row = i - top
            label, p = items[i]
            if i == idx:
                stdscr.attron(curses.color_pair(COLOR_HILITE))
                stdscr.addstr(y + row, 2, label[: w - 4])
                stdscr.attroff(curses.color_pair(COLOR_HILITE))
            else:
                stdscr.addstr(y + row, 2, label[: w - 4])

        draw_footer(stdscr, "Esc: cancel • Ctrl+C: quit • Space: Use this folder")
        stdscr.refresh()
        ch = stdscr.getch()
        if ch in (3,):  # Ctrl+C
            raise KeyboardInterrupt
        if ch in (27,):  # Esc
            return None
        if ch in (curses.KEY_UP,):
            idx = max(0, idx - 1)
        elif ch in (curses.KEY_DOWN,):
            idx = min(len(items) - 1, idx + 1) if items else 0
        elif ch in (curses.KEY_LEFT, curses.KEY_BACKSPACE, 127, 8):
            if cwd.parent != cwd:
                cwd = cwd.parent
                idx = 0
        elif ch in (ord(' '),):
            return cwd
        elif ch in (curses.KEY_ENTER, 10, 13):
            label, p = items[idx]
            if label.startswith("✅"):
                return cwd
            if label.startswith("[..]"):
                if cwd.parent != cwd:
                    cwd = cwd.parent
                    idx = 0
            else:
                if p.is_dir():
                    cwd = p
                    idx = 0
        elif ch == curses.KEY_RESIZE:
            pass


def main_menu(stdscr, source: Optional[str], dest: Path) -> Tuple[Optional[str], Path, bool]:
    """Returns (source, dest, start_now)"""
    curses.curs_set(0)
    options = [
        "Paste magnet link",
        "Pick a .torrent file",
        "Choose download folder",
        "Start download",
        "Quit",
    ]
    idx = 0
    start_now = False
    while True:
        stdscr.clear()
        draw_header(stdscr, " Mint Torrent — Home ")
        h, w = stdscr.getmaxyx()
        y = 2

        src_str = (source[: w - 20] + "…") if (source and len(source) > w - 20) else (source or "— not set —")
        stdscr.addstr(y, 2, f"Source: {src_str}"); y += 1
        stdscr.addstr(y, 2, f"Dest:   {str(dest)[: w - 8]}"); y += 2

        stdscr.addstr(y, 2, "Use ↑/↓ to move, Enter to select. Esc/Ctrl+C to quit."); y += 2

        for i, opt in enumerate(options):
            label = opt
            if opt == "Start download" and not source:
                label = f"{opt} (disabled — set source)"
            if i == idx:
                stdscr.attron(curses.color_pair(COLOR_HILITE))
                stdscr.addstr(y + i, 4, label[: w - 8])
                stdscr.attroff(curses.color_pair(COLOR_HILITE))
            else:
                stdscr.addstr(y + i, 4, label[: w - 8])

        draw_footer(stdscr, "Esc/Ctrl+C: quit")
        stdscr.refresh()

        ch = stdscr.getch()
        if ch in (3,):  # Ctrl+C
            raise KeyboardInterrupt
        if ch in (27,):  # Esc
            return source, dest, False
        if ch in (curses.KEY_UP,):
            idx = (idx - 1) % len(options)
        elif ch in (curses.KEY_DOWN,):
            idx = (idx + 1) % len(options)
        elif ch in (curses.KEY_ENTER, 10, 13):
            choice = options[idx]
            if choice == "Paste magnet link":
                val = text_input(stdscr, "Paste magnet link:")
                if val:
                    source = val
            elif choice == "Pick a .torrent file":
                picked = file_browser(stdscr, Path.home(), ".torrent")
                if picked:
                    source = str(picked.resolve())
            elif choice == "Choose download folder":
                newd = dir_browser(stdscr, Path.home())
                if newd:
                    dest = newd
            elif choice == "Start download":
                if source:
                    start_now = True
                    return source, dest, start_now
            elif choice == "Quit":
                return source, dest, False
        elif ch == curses.KEY_RESIZE:
            pass


def draw_progress_screen(stdscr, name: str, dest: Path, handle: lt.torrent_handle):
    curses.curs_set(0)
    stdscr.nodelay(True)
    spinner = "|/-\\"
    spin_i = 0
    last_draw = 0.0

    while True:
        s = handle.status()

        # Waiting for metadata (magnet)
        if not handle.has_metadata():
            stdscr.clear()
            draw_header(stdscr, " Mint Torrent — Fetching Metadata ")
            h, w = stdscr.getmaxyx()
            msg = f"Please wait… {spinner[spin_i % len(spinner)]}"
            spin_i += 1
            stdscr.addstr(3, 2, f"Name: {name}"[: w - 4])
            stdscr.addstr(4, 2, f"Dest: {str(dest)}"[: w - 4])
            stdscr.addstr(6, 2, msg[: w - 4])
            draw_footer(stdscr, "Esc: cancel • Ctrl+C: quit")
            stdscr.refresh()
            time.sleep(0.1)
            try:
                key = stdscr.getch()
                if key in (27,):  # Esc
                    return False
                if key in (3,):
                    raise KeyboardInterrupt
            except curses.error:
                pass
            continue

        if handle.is_seed():
            stdscr.clear()
            draw_header(stdscr, " Mint Torrent — Complete ")
            h, w = stdscr.getmaxyx()
            stdscr.addstr(3, 2, f"Name: {handle.name()}"[: w - 4])
            stdscr.addstr(4, 2, f"Saved to: {str(dest)}"[: w - 4])
            bar_w = max(10, w - 6)
            stdscr.addstr(6, 2, "[" + ("=" * (bar_w - 2)) + "]", curses.color_pair(COLOR_BAR))
            stdscr.addstr(8, 2, "Download complete! Press any key to exit.")
            draw_footer(stdscr, "Esc/Ctrl+C: exit")
            stdscr.refresh()
            stdscr.nodelay(False)
            stdscr.getch()
            return True

        now = time.time()
        if now - last_draw < 0.1:
            time.sleep(0.05)
            continue
        last_draw = now

        pct = max(0.0, min(100.0, s.progress * 100.0))
        peers = s.num_peers
        down = format_rate(s.download_rate)
        up = format_rate(s.upload_rate)

        stdscr.clear()
        draw_header(stdscr, " Mint Torrent — Downloading ")
        h, w = stdscr.getmaxyx()

        stdscr.addstr(2, 2, f"Name: {handle.name()}"[: w - 4])
        stdscr.addstr(3, 2, f"Dest: {str(dest)}"[: w - 4])

        bar_x = 2
        bar_y = 5
        bar_w = max(20, w - 4)
        fill = int((pct / 100.0) * (bar_w - 2))
        stdscr.addstr(bar_y, bar_x, "[" + " " * (bar_w - 2) + "]")
        stdscr.addstr(bar_y, bar_x + 1, "=" * fill, curses.color_pair(COLOR_BAR))

        stats_line = f"Progress: {pct:6.2f}% | Peers: {peers:>2} | Download rate: {down} | Upload rate: {up}"
        stdscr.addstr(bar_y + 2, 2, stats_line[: w - 4])

        draw_footer(stdscr, "Esc: cancel • Ctrl+C: quit")
        stdscr.refresh()

        try:
            key = stdscr.getch()
            if key in (27,):  # Esc
                return False
            if key in (3,):   # Ctrl+C
                raise KeyboardInterrupt
        except curses.error:
            pass

        time.sleep(0.1)


def curses_flow(source: Optional[str], dest: Optional[Path]) -> Tuple[Optional[str], Optional[Path], Optional[bool]]:
    def _wrapped(stdscr):
        curses.curs_set(0)
        init_colors()
        _src = source
        _dest = dest or default_download_dir()
        _src, _dest, start_now = main_menu(stdscr, _src, _dest)
        return _src, _dest, start_now
    return curses.wrapper(_wrapped)


def curses_progress(source: str, dest: Path) -> bool:
    def _wrapped(stdscr):
        curses.curs_set(0)
        init_colors()
        ses = make_session()
        params = {"save_path": str(dest)}
        if is_magnet(source):
            h = lt.add_magnet_uri(ses, source, params)
            name = "Magnet torrent"
        else:
            info = lt.torrent_info(source)
            params["ti"] = info
            h = ses.add_torrent(params)
            name = info.name()
        ok = draw_progress_screen(stdscr, name, dest, h)
        return ok
    return curses.wrapper(_wrapped)


# ---------- CLI ----------

@app.command(help="Download a torrent via magnet or .torrent. Full-screen TUI with arrow keys.")
def get(
    source: Optional[str] = typer.Argument(
        None, help="Magnet URI or path to .torrent file (optional; TUI opens if omitted)"
    ),
    path: Optional[Path] = typer.Option(
        None, "--path", "-p", help="Download directory (defaults to ~/Downloads/torrents)"
    ),
):
    try:
        if not source:
            src, dest, start = curses_flow(None, path)
            if not start or not src or not dest:
                raise typer.Exit(code=0)
            ok = curses_progress(src, dest)
            raise typer.Exit(code=0 if ok else 1)
        else:
            if not is_magnet(source):
                p = Path(source).expanduser().resolve()
                if not p.exists() or p.suffix.lower() != ".torrent":
                    typer.secho("Error: Provide a magnet link or a path to a .torrent file.", fg="red")
                    raise typer.Exit(code=1)
                source = str(p)
            dest = path or default_download_dir()
            dest.mkdir(parents=True, exist_ok=True)
            ok = curses_progress(source, dest)
            raise typer.Exit(code=0 if ok else 1)

    except KeyboardInterrupt:
        typer.echo("\nInterrupted.")
        raise typer.Exit(code=130)


if __name__ == "__main__":
    app()