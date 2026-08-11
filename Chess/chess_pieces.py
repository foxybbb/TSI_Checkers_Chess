# -*- coding: utf-8 -*-
"""
Stub: piece classes are now backed by chess_engine.py (python-chess).
Kept only so that any remaining  `from chess_pieces import ...`  calls
in external scripts don't produce an ImportError.
"""

from chess_engine import (
    PieceProxy as Piece,
    Pawn, Rook, Knight, Bishop, Queen, King,
)

__all__ = ['Piece', 'Pawn', 'Rook', 'Knight', 'Bishop', 'Queen', 'King']
