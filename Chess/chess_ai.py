# -*- coding: utf-8 -*-
"""
Chess AI powered by Stockfish via python-chess engine protocol.
Falls back to a random legal move if Stockfish is unavailable or not configured.
"""

import os
import glob
import random
import shutil
import chess
import chess.engine
from chess_engine import ChessMove

__all__ = ['getRandomMove', 'getBestMove']

# ── Stockfish configuration ───────────────────────────────────────────────────
# The binary is located automatically, in this order:
#   1. The STOCKFISH_PATH environment variable, if set.
#   2. Any stockfish*.exe inside a 'stockfish' folder next to this script.
#   3. 'stockfish' found on the system PATH.
#   4. The hardcoded fallback below (the robot PC's install).
# To force a specific binary, either set the env var or edit _FALLBACK_PATH.
_FALLBACK_PATH = r"C:\Users\ur3robot\Desktop\stockfish-windows-x86-64-avx2\stockfish\stockfish-windows-x86-64-avx2.exe"


def _find_stockfish():
    env = os.environ.get("STOCKFISH_PATH")
    if env and os.path.isfile(env):
        return env
    here = os.path.dirname(os.path.abspath(__file__))
    base = os.path.join(here, "stockfish")
    for pattern in ("stockfish*.exe",                       # exe placed directly
                    os.path.join("*", "stockfish*.exe"),    # exe one folder deep (extracted zip)
                    "stockfish*"):                          # POSIX binary, no extension
        for hit in glob.glob(os.path.join(base, pattern)):
            if os.path.isfile(hit):                         # never return a directory
                return hit
    on_path = shutil.which("stockfish")
    if on_path:
        return on_path
    return _FALLBACK_PATH


STOCKFISH_PATH = _find_stockfish()

# ╔════════════════════════════════════════════════════════════════════════════╗
# ║                    STOCKFISH DIFFICULTY SETTINGS                         ║
# ║                                                                          ║
# ║  Change these values directly to control how strong the engine plays.    ║
# ║                                                                          ║
# ║  STOCKFISH_SKILL_LEVEL : 0 (weakest) to 20 (strongest)                  ║
# ║  STOCKFISH_DEPTH        : search depth (1=instant, 20=very deep)         ║
# ║  STOCKFISH_TIME_LIMIT   : max seconds per move                          ║
# ╚════════════════════════════════════════════════════════════════════════════╝

STOCKFISH_SKILL_LEVEL = 0    # ◀◀◀ Skill Level: 0 (beginner) – 20 (master)
STOCKFISH_DEPTH       = 10     # ◀◀◀ Search depth: lower = weaker/faster
STOCKFISH_TIME_LIMIT  = 1.0    # ◀◀◀ Max time in seconds per move

# Named difficulty presets used by the GUI difficulty selector.
DIFFICULTY_PRESETS = {
    "Easy":   dict(skill=0,  depth=5,  time=0.3),
    "Medium": dict(skill=8,  depth=10, time=0.8),
    "Hard":   dict(skill=15, depth=15, time=1.5),
    "Max":    dict(skill=20, depth=20, time=2.5),
}


def set_difficulty(skill=None, depth=None, time=None):
    """Update the engine strength at runtime (used by the difficulty selector)."""
    global STOCKFISH_SKILL_LEVEL, STOCKFISH_DEPTH, STOCKFISH_TIME_LIMIT
    if skill is not None:
        STOCKFISH_SKILL_LEVEL = int(skill)
    if depth is not None:
        STOCKFISH_DEPTH = int(depth)
    if time is not None:
        STOCKFISH_TIME_LIMIT = float(time)


def set_difficulty_preset(name):
    """Apply a named preset from DIFFICULTY_PRESETS. Returns True if applied."""
    preset = DIFFICULTY_PRESETS.get(name)
    if preset:
        set_difficulty(**preset)
        return True
    return False


def get_settings():
    """Return the current (skill, depth, time) as a dict."""
    return dict(skill=STOCKFISH_SKILL_LEVEL, depth=STOCKFISH_DEPTH,
                time=STOCKFISH_TIME_LIMIT)


# ── Public API ────────────────────────────────────────────────────────────────

def getRandomMove(valid_moves):
    """Pick and return a random move from the list of legal moves."""
    if not valid_moves:
        return None
    return random.choice(valid_moves)


def getBestMove(gs):
    """
    Ask Stockfish for the best move in the current position.

    ╔══════════════════════════════════════════════════════════════════════╗
    ║  Uses STOCKFISH_SKILL_LEVEL, STOCKFISH_DEPTH, STOCKFISH_TIME_LIMIT ║
    ║  to control how strong the engine plays.                           ║
    ║                                                                    ║
    ║  Skill Level 0  = very weak (blunders frequently)                  ║
    ║  Skill Level 20 = full strength                                    ║
    ╚══════════════════════════════════════════════════════════════════════╝

    Parameters
    ----------
    gs : chess_engine.GameState

    Returns
    -------
    ChessMove | None
        A ChessMove adapter for the best move, or None if Stockfish is not
        configured / fails (the caller in chess_main.py will then fall back
        to getRandomMove).
    """
    if not STOCKFISH_PATH:
        print("[AI] STOCKFISH_PATH is not set — falling back to random move.")
        return None

    board = gs._board.copy()
    if not board.legal_moves:
        return None

    try:
        with chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH) as engine:
            # ┌──────────────────────────────────────────────────────────┐
            # │  APPLY DIFFICULTY: set Skill Level via UCI options       │
            # │  This makes Stockfish intentionally play weaker moves    │
            # │  at lower skill levels.                                  │
            # └──────────────────────────────────────────────────────────┘
            engine.configure({"Skill Level": STOCKFISH_SKILL_LEVEL})

            result = engine.play(
                board,
                chess.engine.Limit(
                    time=STOCKFISH_TIME_LIMIT,
                    depth=STOCKFISH_DEPTH,         # ◀◀◀ depth limit
                )
            )
        if result.move:
            return ChessMove(result.move, board)
    except FileNotFoundError:
        print(f"[AI] Stockfish binary not found at: {STOCKFISH_PATH!r}")
    except Exception as e:
        print(f"[AI] Stockfish error: {e}")

    return None
