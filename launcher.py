#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Robotic Board Games — top-level launcher.

Lets the operator choose which game to run against the physical board / UR3
robot: Checkers or Chess. Each game lives in its own folder with its own
dependencies; this launcher finds the right Python interpreter (a local
virtual-env if present, otherwise the current interpreter) and starts the game
in its own working directory.

Run it with:  python launcher.py     (or double-click run.bat on Windows)
"""

import os
import subprocess
import sys
import tkinter as tk
from tkinter import messagebox, ttk

# ── High-DPI awareness (Windows) — do this before any Tk window is built ──────
def enable_dpi_awareness():
    if sys.platform != "win32":
        return
    try:
        import ctypes
        try:
            # Per-monitor-v2 if available (Win 8.1+/10), else system-DPI aware.
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


HERE = os.path.dirname(os.path.abspath(__file__))

# ── Colours ───────────────────────────────────────────────────────────────────
BG = "#20232a"
CARD = "#2c313c"
ACCENT = "#d2b48c"
TXT = "#f3ece4"
SUBTXT = "#a9b0bd"


def find_python(game_dir: str) -> str:
    """Return the best interpreter for a game: its own venv if present."""
    candidates = [
        os.path.join(game_dir, ".venv", "Scripts", "python.exe"),   # Windows venv
        os.path.join(game_dir, ".venv", "bin", "python"),           # POSIX venv
        os.path.join(game_dir, "venv", "Scripts", "python.exe"),
        os.path.join(game_dir, "venv", "bin", "python"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return sys.executable


GAMES = {
    "checkers": {
        "title": "Checkers",
        "subtitle": "Draughts vs. computer • Arduino board + UR3 robot",
        "dir": os.path.join(HERE, "Checkers"),
        "script": "main.py",
    },
    "chess": {
        "title": "Chess",
        "subtitle": "Stockfish AI • Arduino board + UR3 robot",
        "dir": os.path.join(HERE, "Chess"),
        "script": "chess_main.py",
    },
}


def launch(game_key: str, root: tk.Tk):
    game = GAMES[game_key]
    game_dir = game["dir"]
    script = os.path.join(game_dir, game["script"])
    if not os.path.isfile(script):
        messagebox.showerror(
            "Game not found",
            f"Could not find {game['script']} in:\n{game_dir}\n\n"
            "Make sure the game folder is next to this launcher.",
        )
        return
    python = find_python(game_dir)
    try:
        # Start the game detached in its own working directory.
        subprocess.Popen([python, script], cwd=game_dir)
    except Exception as e:
        messagebox.showerror(
            "Could not start game",
            f"Failed to launch {game['title']}.\n\n{e}\n\n"
            f"Interpreter: {python}",
        )
        return
    root.destroy()


def main():
    enable_dpi_awareness()
    root = tk.Tk()
    root.title("Robotic Board Games — Launcher")
    root.configure(bg=BG)
    root.minsize(520, 460)

    try:
        root.tk.call("tk", "scaling", 1.25)
    except Exception:
        pass

    # Centre on screen
    root.update_idletasks()
    w, h = 560, 520
    x = (root.winfo_screenwidth() - w) // 2
    y = (root.winfo_screenheight() - h) // 3
    root.geometry(f"{w}x{h}+{x}+{y}")

    tk.Label(root, text="Robotic Board Games", bg=BG, fg=TXT,
             font=("Segoe UI", 22, "bold")).pack(pady=(28, 4))
    tk.Label(root, text="Choose a game to play on the physical board",
             bg=BG, fg=SUBTXT, font=("Segoe UI", 11)).pack(pady=(0, 24))

    def make_card(key):
        game = GAMES[key]
        card = tk.Frame(root, bg=CARD, highlightbackground=ACCENT,
                        highlightthickness=1)
        card.pack(fill="x", padx=40, pady=10)
        inner = tk.Frame(card, bg=CARD)
        inner.pack(fill="x", padx=18, pady=16)
        tk.Label(inner, text=game["title"], bg=CARD, fg=TXT,
                 font=("Segoe UI", 17, "bold"), anchor="w").pack(fill="x")
        tk.Label(inner, text=game["subtitle"], bg=CARD, fg=SUBTXT,
                 font=("Segoe UI", 10), anchor="w").pack(fill="x", pady=(2, 10))
        btn = tk.Button(inner, text=f"▶  Play {game['title']}", bg=ACCENT,
                        fg="#20232a", font=("Segoe UI", 12, "bold"),
                        activebackground="#e6cfa8", relief="flat", height=1,
                        cursor="hand2", command=lambda: launch(key, root))
        btn.pack(fill="x")
        # Whole card is clickable too
        for wgt in (card, inner):
            wgt.bind("<Button-1>", lambda e, k=key: launch(k, root))
        return card

    make_card("checkers")
    make_card("chess")

    footer = tk.Label(
        root,
        text="Each game opens its own settings window before starting.",
        bg=BG, fg=SUBTXT, font=("Segoe UI", 9))
    footer.pack(side="bottom", pady=16)

    root.bind("<Escape>", lambda e: root.destroy())
    root.mainloop()


if __name__ == "__main__":
    main()
