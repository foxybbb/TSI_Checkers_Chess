# -*- coding: utf-8 -*-
"""
Persistent settings for the Chess game.

Stores operator preferences (COM port, difficulty, time control, robot
host/port, window scale, DPI, full-screen) in a small JSON file next to the
program so they survive restarts. All access goes through load()/save().
"""

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS_PATH = os.path.join(HERE, "chess_settings.json")

DEFAULTS = {
    # ── Board / robot connection ──
    "serial_port": "COM21",
    "baud": 9600,
    "robot_host": "0.0.0.0",     # we host; the UR3 robot connects to us
    "robot_port": 3000,

    # ── Gameplay ──
    "difficulty": "Easy",
    "robot_mode": True,          # True -> send the AI (black) move to the robot
    "time_control": "15 | 0",    # label from the presets table

    # ── Display / scaling ──
    "window_scale": 1.0,         # initial window size = base size x scale
    "dpi_aware": True,           # ask Windows for per-monitor DPI awareness
    "start_fullscreen": False,
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
