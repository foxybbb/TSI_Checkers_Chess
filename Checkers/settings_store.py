# -*- coding: utf-8 -*-
"""
Persistent settings for the Checkers game.

Stores operator preferences (board scale, DPI, window size, COM port, robot
host/port, connection mode, theme, fullscreen) in a small JSON file next to the
program so they survive restarts. All access goes through load()/save() so the
rest of the program never touches the file directly.
"""

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS_PATH = os.path.join(HERE, "checkers_settings.json")

DEFAULTS = {
    # ── Board / robot connection ──
    "connection_mode": "serial",     # "serial" | "tcp"
    "serial_port": "COM20",          # board COM port (paired end of emulator)
    "baud": 9600,
    "tcp_target": "127.0.0.1:5006",  # emulator TCP board bridge (BOARD_TCP)
    "robot_host": "0.0.0.0",         # we host; the UR3 robot connects to us
    "robot_port": 3000,

    # ── Display / scaling ──
    "cell_size": 80,                 # board square size in pixels (scale)
    "ui_scale": 1.0,                 # tk scaling multiplier (fonts/widgets)
    "dpi_aware": True,               # ask Windows for per-monitor DPI awareness
    "start_fullscreen": False,
    "theme": "Wood",                 # "Wood" | "Dark" | "Slate"

    # ── Gameplay ──
    "auto_poll": True,               # keep polling the sensor board live
}


def load() -> dict:
    """Return a settings dict (defaults merged with any saved overrides)."""
    data = dict(DEFAULTS)
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            saved = json.load(f)
        if isinstance(saved, dict):
            for k, v in saved.items():
                if k in DEFAULTS:
                    data[k] = v
    except (FileNotFoundError, ValueError, OSError):
        pass
    return data


def save(data: dict) -> None:
    """Persist the given settings dict (only known keys are written)."""
    out = {k: data.get(k, DEFAULTS[k]) for k in DEFAULTS}
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2)
    except OSError as e:
        print(f"[SETTINGS] Could not save settings: {e}")
