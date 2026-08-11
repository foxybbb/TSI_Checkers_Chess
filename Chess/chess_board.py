# -*- coding: utf-8 -*-
"""
Stub: Board and Square classes are now backed by chess_engine.py (python-chess).
Kept only so that any remaining  `from chess_board import ...`  calls
in external scripts don't produce an ImportError.
"""

from chess_engine import (
    SquareProxy as Square,
    BoardProxy  as Board,
)

__all__ = ['Square', 'Board']


def makeStandardBoard():
    """Stub — use chess_engine.GameState() which sets up the starting position."""
    raise NotImplementedError(
        "makeStandardBoard() is no longer used. "
        "Create a chess_engine.GameState() instead."
    )
