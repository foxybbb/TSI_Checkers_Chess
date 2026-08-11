# -*- coding: utf-8 -*-
"""
Minimal Move stub.

The only remaining use of this class is inside find_move_from_snapshots()
in chess_main.py, which builds a lightweight candidate move from two
SquareProxy objects so it can be matched against the list of legal ChessMoves.
All actual chess logic lives in chess_engine.ChessMove (python-chess).
"""

from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from chess_engine import SquareProxy, PieceProxy


class Move:
    """
    Lightweight candidate-move holder used only by find_move_from_snapshots().

    It is never pushed to the board — the caller matches it against the list
    of legal ChessMove objects (by comparing start/end squares) and then
    executes the matched ChessMove instead.
    """

    def __init__(
        self,
        start_square,
        end_square,
        move_number: int,
        promotion: str = '',
        enpassant_sq=None,
        castle=None,
    ) -> None:
        self.start_square     = start_square
        self.end_square       = end_square
        self.move_number      = move_number
        self.piece_moved      = start_square.get_piece() if start_square else None
        self.piece_captured   = end_square.get_piece()   if end_square   else None
        self.promotion        = promotion
        self.promotion_piece  = None
        self.enpassant_square = enpassant_sq
        self.castle           = castle
        self.name             = ''

    def contains_castle(self)    -> bool: return self.castle is not None
    def contains_enpassant(self) -> bool: return self.enpassant_square is not None
    def contains_promotion(self) -> bool: return bool(self.promotion)

    def get_chess_notation(self, gs=None) -> str:
        if self.name:
            return self.name
        try:
            return self.end_square.get_name()
        except Exception:
            return '?'

    def __eq__(self, other) -> bool:
        if isinstance(other, Move):
            return (self.start_square == other.start_square
                    and self.end_square == other.end_square)
        return False

    def __hash__(self) -> int:
        return hash((self.start_square, self.end_square))

    def __repr__(self) -> str:
        try:
            return (f"Move({self.start_square.get_name()} -> "
                    f"{self.end_square.get_name()})")
        except Exception:
            return "Move(?->?)"
