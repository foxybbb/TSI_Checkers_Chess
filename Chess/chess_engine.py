# -*- coding: utf-8 -*-
"""
Chess engine adapter using python-chess.

Provides exactly the same public interface as the original chess_engine.py
so that chess_main.py (UI + hardware) requires only minimal changes.
All chess logic (legality, check, checkmate, stalemate, pins, en-passant,
castling, promotion) is 100% delegated to the python-chess library.
"""

import chess
from typing import Optional, List, Tuple


# ── Coordinate helpers ────────────────────────────────────────────────────────
#
# Internal coordinate system (same as the original code):
#   col  = file index  0=a … 7=h
#   row  = rank index  0=rank8(top) … 7=rank1(bottom)
#
# python-chess square:
#   chess.square(file_index, rank_index)
#   where rank_index 0=rank1 … 7=rank8
#
# Conversion:   pc_rank = 7 - row

def _to_pc_sq(col: int, row: int) -> int:
    """Internal (col, row) → python-chess square index."""
    return chess.square(col, 7 - row)


def _from_pc_sq(sq: int) -> Tuple[int, int]:
    """python-chess square index → internal (col, row)."""
    return chess.square_file(sq), 7 - chess.square_rank(sq)


# ── Piece proxy ───────────────────────────────────────────────────────────────
_PC_NAME = {
    chess.PAWN:   'Pawn',
    chess.ROOK:   'Rook',
    chess.KNIGHT: 'Knight',
    chess.BISHOP: 'Bishop',
    chess.QUEEN:  'Queen',
    chess.KING:   'King',
}
_PC_SYMBOL = {
    chess.PAWN:   'P',
    chess.ROOK:   'R',
    chess.KNIGHT: 'N',
    chess.BISHOP: 'B',
    chess.QUEEN:  'Q',
    chess.KING:   'K',
}
_PROMO_FROM_PC = {
    chess.QUEEN:  'Queen',
    chess.ROOK:   'Rook',
    chess.BISHOP: 'Bishop',
    chess.KNIGHT: 'Knight',
}


class PieceProxy:
    """
    Wraps a chess.Piece and exposes the interface expected by chess_main.py
    (get_name, get_color, get_image_name, get_symbol, is_on_board, …).
    """

    def __init__(self, chess_piece: chess.Piece) -> None:
        self._piece = chess_piece
        self.name       = _PC_NAME[chess_piece.piece_type]
        self.symbol     = _PC_SYMBOL[chess_piece.piece_type]
        self.color      = 'white' if chess_piece.color == chess.WHITE else 'black'
        # Image key used by loadImages(): first letter of color + symbol  e.g. 'wP', 'bK'
        self.image_name = self.color[0] + self.symbol
        self.square     = None   # set by caller when needed

    # --- interface methods -----------------------------------------------
    def get_name(self)       -> str:  return self.name
    def get_color(self)      -> str:  return self.color
    def get_image_name(self) -> str:  return self.image_name
    def get_symbol(self)     -> str:  return self.symbol
    def get_fullname(self)   -> str:  return f"{self.color.title()} {self.name}"
    def is_on_board(self)    -> bool: return True
    def has_moved(self)      -> bool: return True   # not tracked; safe default
    def get_first_move(self)          : return None
    def is_pinned(self)      -> bool: return False
    def get_pin_direction(self)       : return ()
    def get_coords(self)              :
        return self.square.get_coords() if self.square else ()
    def get_square(self)              : return self.square
    def get_square_name(self) -> str  :
        return self.square.get_name() if self.square else ''
    def can_promote(self)    -> bool: return False  # handled by python-chess

    def __repr__(self) -> str:
        return f"PieceProxy({self.color} {self.name})"

    def __eq__(self, other) -> bool:
        if isinstance(other, PieceProxy):
            return (self._piece == other._piece
                    and id(self) == id(other))
        return False

    def __hash__(self) -> int:
        return hash(id(self))


# ── Square proxy ──────────────────────────────────────────────────────────────
_FILE_LETTERS = 'abcdefgh'
_RANK_NUMBERS  = '87654321'   # row 0 → '8',  row 7 → '1'


class SquareProxy:
    """
    Represents one square on the board.
    Reads piece information live from a chess.Board so it is always
    up to date without needing to rebuild the object after every move.
    """

    def __init__(self, col: int, row: int, board: chess.Board) -> None:
        self.col   = col
        self.row   = row
        self._board = board
        self.name  = _FILE_LETTERS[col] + _RANK_NUMBERS[row]
        # Legacy attribute names
        self.file  = col
        self.rank  = row
        self.color = 'light' if (col + row) % 2 == 0 else 'dark'

    # --- piece access ---------------------------------------------------
    def get_piece(self) -> Optional[PieceProxy]:
        pc = self._board.piece_at(_to_pc_sq(self.col, self.row))
        if pc is None:
            return None
        proxy = PieceProxy(pc)
        proxy.square = self
        return proxy

    @property
    def piece(self) -> Optional[PieceProxy]:
        """Direct attribute access used by drawPieces()."""
        return self.get_piece()

    def has_piece(self) -> bool:
        return self._board.piece_at(_to_pc_sq(self.col, self.row)) is not None

    def has_friendly_piece(self, ref) -> bool:
        pc = self.get_piece()
        return bool(pc and ref and pc.get_color() == ref.get_color())

    def has_enemy_piece(self, ref) -> bool:
        pc = self.get_piece()
        return bool(pc and ref and pc.get_color() != ref.get_color())

    def set_piece(self, piece) -> None:
        """No-op – board state is owned by chess.Board."""
        pass

    def remove_piece(self) -> None:
        """No-op – board state is owned by chess.Board."""
        pass

    # --- coordinate helpers ---------------------------------------------
    def get_file(self)    -> int:           return self.col
    def get_rank(self)    -> int:           return self.row
    def get_coords(self)  -> Tuple[int, int]: return self.col, self.row
    def get_name(self)    -> str:           return self.name
    def get_color(self)   -> str:           return self.color
    def get_board(self)                   : return None   # not needed

    # --- equality & hash (coordinate-based) -----------------------------
    def __eq__(self, other) -> bool:
        if isinstance(other, SquareProxy):
            return self.col == other.col and self.row == other.row
        return False

    def __hash__(self) -> int:
        return hash((self.col, self.row))

    def __repr__(self) -> str:
        return f"SquareProxy({self.name})"

    def __str__(self) -> str:
        return self.name


# ── Squares proxy (supports board.squares[col, row]) ─────────────────────────
class SquaresProxy:
    """Supports squares[col, row] tuple-index backed by a live chess.Board."""

    def __init__(self, board_ref: 'BoardProxy') -> None:
        self._bp = board_ref

    def __getitem__(self, key):
        col, row = key
        return SquareProxy(col, row, self._bp._board)

    @property
    def flat(self):
        """Iterate all 64 squares in row-major order (for compatibility)."""
        for row in range(8):
            for col in range(8):
                yield SquareProxy(col, row, self._bp._board)


# ── Board proxy ───────────────────────────────────────────────────────────────
class BoardProxy:
    """
    Wraps a chess.Board and exposes the interface expected by chess_main.py.
    """

    def __init__(self, chess_board: chess.Board) -> None:
        self._board  = chess_board
        self.squares = SquaresProxy(self)
        # Stub attributes that chess_main may probe with hasattr()
        self.white_king = None
        self.black_king = None
        self.piece_lists: dict = {}

    def get_pieces(self, color: str = None) -> List[PieceProxy]:
        pieces = []
        for sq in chess.SQUARES:
            pc = self._board.piece_at(sq)
            if pc is None:
                continue
            col, row = _from_pc_sq(sq)
            proxy = PieceProxy(pc)
            proxy.square = SquareProxy(col, row, self._board)
            if color is None or proxy.color == color:
                pieces.append(proxy)
        return pieces

    def get_size(self) -> Tuple[int, int]:
        return 8, 8

    # Stubs so that old code that calls board.update_pieces() doesn't crash
    def update_pieces(self, *args, **kwargs) -> None:
        pass


# ── Move adapter ──────────────────────────────────────────────────────────────
class ChessMove:
    """
    Adapts a chess.Move to the interface expected by chess_main.py and
    RobotHandler.send_move().

    board_before must be a chess.Board snapshot taken BEFORE the move is pushed.
    """

    def __init__(self, chess_move: chess.Move, board_before: chess.Board) -> None:
        self._move   = chess_move
        self._board  = board_before   # snapshot used for notation & piece lookups

        from_col, from_row = _from_pc_sq(chess_move.from_square)
        to_col,   to_row   = _from_pc_sq(chess_move.to_square)

        self.start_square = SquareProxy(from_col, from_row, board_before)
        self.end_square   = SquareProxy(to_col,   to_row,   board_before)

        moving_pc   = board_before.piece_at(chess_move.from_square)
        captured_pc = board_before.piece_at(chess_move.to_square)

        self.piece_moved    = PieceProxy(moving_pc)   if moving_pc   else None
        self.piece_captured = PieceProxy(captured_pc) if captured_pc else None

        # Set square references on piece proxies
        if self.piece_moved:
            self.piece_moved.square = self.start_square
        if self.piece_captured:
            self.piece_captured.square = self.end_square

        # --- en passant -------------------------------------------------
        if board_before.is_en_passant(chess_move):
            ep_col  = to_col
            ep_row  = from_row   # captured pawn sits on the mover's rank
            ep_pc   = board_before.piece_at(_to_pc_sq(ep_col, ep_row))
            self.enpassant_square = SquareProxy(ep_col, ep_row, board_before)
            self.piece_captured   = PieceProxy(ep_pc) if ep_pc else None
            if self.piece_captured:
                self.piece_captured.square = self.enpassant_square
        else:
            self.enpassant_square = None

        # --- promotion --------------------------------------------------
        if chess_move.promotion:
            self.promotion = _PROMO_FROM_PC.get(chess_move.promotion, 'Queen')
            promo_color    = moving_pc.color if moving_pc else chess.WHITE
            self.promotion_piece = PieceProxy(
                chess.Piece(chess_move.promotion, promo_color))
        else:
            self.promotion       = ''
            self.promotion_piece = None

        # --- castling ---------------------------------------------------
        if board_before.is_castling(chess_move):
            kingside   = board_before.is_kingside_castling(chess_move)
            rank_row   = from_row
            rook_f_col = 7 if kingside else 0
            rook_t_col = 5 if kingside else 3
            rook_f_sq  = SquareProxy(rook_f_col, rank_row, board_before)
            rook_t_sq  = SquareProxy(rook_t_col, rank_row, board_before)
            rook_pc    = board_before.piece_at(_to_pc_sq(rook_f_col, rank_row))
            rook_proxy = PieceProxy(rook_pc) if rook_pc else None
            if rook_proxy:
                rook_proxy.square = rook_f_sq
            self.castle = (rook_proxy, rook_f_sq, rook_t_sq)
        else:
            self.castle = None

        self.move_number = 0   # updated externally after appending to move_log
        self.name        = ''

    # --- predicates -----------------------------------------------------
    def contains_castle(self)    -> bool: return self.castle is not None
    def contains_enpassant(self) -> bool: return self.enpassant_square is not None
    def contains_promotion(self) -> bool: return bool(self.promotion)

    # --- notation -------------------------------------------------------
    def get_chess_notation(self, gs=None) -> str:
        if self.name:
            return self.name
        try:
            return self._board.san(self._move)
        except Exception:
            return self._move.uci()

    # --- dunder ---------------------------------------------------------
    def __eq__(self, other) -> bool:
        if isinstance(other, ChessMove):
            return self._move == other._move
        return False

    def __hash__(self) -> int:
        return hash(self._move)

    def __repr__(self) -> str:
        return f"ChessMove({self._move.uci()})"

    def __str__(self) -> str:
        return self.name or self._move.uci()


# ── Game State ────────────────────────────────────────────────────────────────
class GameState:
    """
    Wraps chess.Board and exposes the same interface as the original GameState.
    All chess logic is handled by python-chess (100% correct legality checks).
    """

    def __init__(self) -> None:
        self._board      = chess.Board()   # standard starting position
        self.board       = BoardProxy(self._board)
        self.move_log    : List[ChessMove] = []
        self.undo_log    : List[ChessMove] = []
        self.move_branches               = []
        self.gameover    = False
        self.valid_moves : List[ChessMove] = []
        self.enpassant_coords = ()   # kept for API compat; not used internally

    # --- read-only properties mirroring old API -------------------------
    @property
    def white_to_move(self) -> bool:
        return self._board.turn == chess.WHITE

    @property
    def in_check(self) -> bool:
        return self._board.is_check()

    @property
    def checkmate(self) -> bool:
        return self._board.is_checkmate()

    @property
    def stalemate(self) -> bool:
        return (self._board.is_stalemate()
                or self._board.is_insufficient_material()
                or self._board.is_fifty_moves()
                or self._board.is_fivefold_repetition())

    @property
    def move_number(self) -> int:
        return len(self.move_log)

    # --- move generation -----------------------------------------------
    def get_valid_moves(self) -> List[ChessMove]:
        """Return all legal moves for the side to move."""
        snap  = self._board.copy()
        moves = [ChessMove(m, snap) for m in self._board.legal_moves]
        base  = len(self.move_log)
        for i, m in enumerate(moves):
            m.move_number = base + i
        self.valid_moves = moves
        return moves

    def get_all_possible_moves(self) -> List[ChessMove]:
        """Alias used by some callers."""
        return self.get_valid_moves()

    def get_all_moves(self) -> List[ChessMove]:
        """Alias used by some callers."""
        return self.get_valid_moves()

    # --- move execution ------------------------------------------------
    def make_move(self, move: ChessMove) -> None:
        """Apply a move (does NOT clear undo_log — use make_new_move for that)."""
        if not isinstance(move, ChessMove):
            return
        self._board.push(move._move)
        self.board = BoardProxy(self._board)
        self.move_log.append(move)

    def make_new_move(self, move: ChessMove) -> None:
        """Apply a new player/AI move and clear the redo log."""
        if self.undo_log:
            self.move_branches.append(list(self.undo_log))
            self.undo_log.clear()
        self.make_move(move)

    def undo_move(self) -> None:
        """Undo the last move."""
        if self.move_log:
            self._board.pop()
            self.board = BoardProxy(self._board)
            moved = self.move_log.pop()
            self.undo_log.append(moved)
            if self.gameover:
                self.gameover = False

    def redo_move(self) -> None:
        """Redo a previously undone move."""
        if self.undo_log:
            move = self.undo_log.pop()
            self.make_move(move)

    # --- check helpers (legacy API) ------------------------------------
    def is_in_check(self) -> bool:
        return self._board.is_check()

    def square_under_attack(self, square: SquareProxy) -> bool:
        pc_sq = _to_pc_sq(square.col, square.row)
        color = chess.WHITE if self.white_to_move else chess.BLACK
        return self._board.is_attacked_by(not color, pc_sq)

    # --- board reset / sync --------------------------------------------
    def force_clear(self) -> None:
        """Completely clear the board (called before Arduino sync)."""
        self._board.clear()
        self.board       = BoardProxy(self._board)
        self.move_log    = []
        self.undo_log    = []
        self.gameover    = False
        self.valid_moves = []

    def sync_from_arduino(self, arduino_data: list) -> None:
        """
        Rebuild the board from a list of 64 dicts produced by
        ArduinoHandler._parse_raw_data().

        Index layout: index = row * 8 + col
            row 0  = rank 8 (top of board, Black's back rank)
            row 7  = rank 1 (bottom, White's back rank)
        """
        _PIECE_FEN = {
            'Pawn':   'p',
            'Rook':   'r',
            'Knight': 'n',
            'Bishop': 'b',
            'Queen':  'q',
            'King':   'k',
        }

        fen_rows = []
        for row in range(8):
            empty   = 0
            row_str = ''
            for col in range(8):
                cell = arduino_data[row * 8 + col]
                if cell is None:
                    empty += 1
                else:
                    if empty:
                        row_str += str(empty)
                        empty = 0
                    ch = _PIECE_FEN.get(cell['type'], 'p')
                    if cell['color'] == 'white':
                        ch = ch.upper()
                    row_str += ch
            if empty:
                row_str += str(empty)
            fen_rows.append(row_str)

        position = '/'.join(fen_rows)

        # Try to preserve castling rights for standard starting positions.
        # Fall back to no castling rights if the FEN would be invalid.
        set_ok = False
        for castling in ('KQkq', 'kq', 'KQ', ''):
            fen = f"{position} w {castling or '-'} - 0 1"
            try:
                self._board.set_fen(fen)
                set_ok = True
                break
            except ValueError:
                continue

        if not set_ok:
            print("[GameState] Could not build valid FEN from Arduino data — "
                  "starting fresh.")
            self._board = chess.Board()

        self.move_log    = []
        self.undo_log    = []
        self.gameover    = False
        self.board       = BoardProxy(self._board)
        self.valid_moves = self.get_valid_moves()

        print(f"[GameState] Arduino sync complete. FEN: {self._board.fen()}")
        print(f"[GameState] Legal moves available: "
              f"{self._board.legal_moves.count()}")

    # --- legacy stubs (called by old AI code still in chess_main) ------
    def get_pins_and_checks(self, king=None, king_square=None):
        """Stub — python-chess handles this internally."""
        return [], []

    def get_castle_moves(self, king, moves):
        """Stub — castling is already included in get_valid_moves()."""
        pass

    def get_directions(self) -> dict:
        return {
            'HORIZONTAL': ((1, 0), (-1, 0)),
            'VERTICAL':   ((0, 1), (0, -1)),
            'DIAGONAL':   ((1, 1), (1, -1), (-1, 1), (-1, -1)),
            'KNIGHT':     ((2, 1), (2, -1), (-2, 1), (-2, -1),
                           (1, 2), (1, -2), (-1, 2), (-1, -2)),
        }


# ── Dummy piece classes exported for import compatibility ─────────────────────
# chess_main.py does:  from chess_engine import Pawn, Rook, Knight, …
# These classes are no longer used for any logic; they only prevent ImportError.

class _DummyPiece:
    def __init__(self, color: str) -> None:
        self.color = color

class Pawn(_DummyPiece):   pass
class Rook(_DummyPiece):   pass
class Knight(_DummyPiece): pass
class Bishop(_DummyPiece): pass
class Queen(_DummyPiece):  pass
class King(_DummyPiece):   pass
