# -*- coding: utf-8 -*-
"""
Chess Board + UR3 Robot Emulator
================================

A small Tkinter test bench that replaces the *physical hardware* of the chess
robot so the whole system can be exercised with no real board and no real UR3.

It provides two independent interfaces around one shared on-screen board:

  1. BOARD ("works like the Arduino")
     Something scans the board by sending the byte 'a'; we answer with a line of
     64 space-separated integers (index = row*8+col, row 0 = rank 8):
         0                      -> empty
         (type_id<<1)|color_bit -> piece   (white color_bit = 1)
         type_id: Pawn 2, Rook 3, Knight 4, Bishop 5, Queen 6, King 7
     Transport:
         * com0com virtual COM pair (recommended on Windows) -> real COM port,
           chess_main opens the other end unchanged.
         * TCP bridge (no driver) -> chess_main uses board_tcp_serial.TcpSerial.
         * virtualserialports / PTY on Linux/WSL.

  2. ROBOT SOCKET (host 192.168.0.100, port 3000)
     SERVER mode (default): the emulator hosts the socket; the UR3 robot connects
     and we push every board move to it as a 6-byte packet:
         struct '6B' = (type, x0, y0, x1, y1, promotion)   coords in [1..8]
         x = col+1, y = 8-row
         type 1=move 2=capture 3=en-passant 4=O-O 5=O-O-O   (castle coords = 1)
         promotion 0=none 1=Queen 2=Rook 3=Bishop 4=Knight
     CLIENT mode: the emulator instead connects to chess_main's server as the
     robot, sends "MOVE" and applies the move packets it receives.

Requires: pyserial (COM side).  com0com for the virtual COM pair on Windows.
"""

import os
import sys
import struct
import socket
import threading
import queue
import tkinter as tk
from tkinter import ttk, messagebox

try:
    import serial
    import serial.tools.list_ports as list_ports
except ImportError:
    serial = None
    list_ports = None

import com0com_setup

# --------------------------------------------------------------------------- #
#  Defaults
# --------------------------------------------------------------------------- #
DEFAULT_BAUD       = 9600
DEFAULT_TCP_BOARD  = ("127.0.0.1", 5005)
DEFAULT_ROBOT_HOST = "127.0.0.1"    # default to local loopback for the emulator
DEFAULT_ROBOT_PORT = 3000
HINT_ROBOT_IP      = "192.168.0.100"     # from the project whiteboard

SQUARE   = 60
MARGIN   = 24
BOARD_PX = SQUARE * 8

COLOR_LIGHT      = "#EEEED2"
COLOR_DARK       = "#769656"
COLOR_SELECT     = "#F6F669"
COLOR_LASTMOVE_L = "#F7EC74"
COLOR_LASTMOVE_D = "#BBCB2B"
COLOR_LABEL      = "#DDDDDD"

# --------------------------------------------------------------------------- #
#  Piece helpers  (cell = None or 2-char code: 'w'/'b' + P R N B Q K)
# --------------------------------------------------------------------------- #
TYPE_ID = {"P": 2, "R": 3, "N": 4, "B": 5, "Q": 6, "K": 7}
GLYPH = {
    "wK": "♔", "wQ": "♕", "wR": "♖",
    "wB": "♗", "wN": "♘", "wP": "♙",
    "bK": "♚", "bQ": "♛", "bR": "♜",
    "bB": "♝", "bN": "♞", "bP": "♟",
}
PROMO_CODE = {"Q": 1, "R": 2, "B": 3, "N": 4}     # piece letter -> protocol id
PROMO_FROM_CODE = {1: "Q", 2: "R", 3: "B", 4: "N"}
MOVE_TYPE_NAME = {1: "MOVE", 2: "CAPTURE", 3: "EN-PASSANT",
                  4: "CASTLE O-O", 5: "CASTLE O-O-O"}

FILES = "abcdefgh"
RANKS = "87654321"          # row 0 -> '8'


def sq_name(row, col):
    return f"{FILES[col]}{RANKS[row]}"


# Sentinel returned by piece_picker when the user cancels (vs. None = clear square)
PICK_CANCEL = "__cancel__"


def piece_picker(parent, square_name, rows, glyph_font=("Segoe UI Symbol", 22)):
    """
    Modal dialog to set ANY figure on a square. `rows` is a list of rows, each a
    list of (label, value, fg). Returns the chosen value, None (clear), or
    PICK_CANCEL (no change).
    """
    import tkinter as tk
    result = {"v": PICK_CANCEL}
    win = tk.Toplevel(parent)
    win.title(f"Set {square_name}")
    win.configure(bg="#2B2B2B")
    win.attributes("-topmost", True)
    win.resizable(False, False)
    tk.Label(win, text=f"Set figure on  {square_name}", bg="#2B2B2B", fg="white",
             font=("Segoe UI", 11, "bold")).pack(padx=16, pady=(12, 8))
    grid = tk.Frame(win, bg="#2B2B2B")
    grid.pack(padx=12)

    def choose(v):
        result["v"] = v
        win.destroy()

    for r, row in enumerate(rows):
        for c, (label, value, fg) in enumerate(row):
            tk.Button(grid, text=label, width=3, font=glyph_font, fg=fg, bg="#EEE",
                      command=lambda v=value: choose(v)).grid(row=r, column=c, padx=3, pady=3)
    bottom = tk.Frame(win, bg="#2B2B2B")
    bottom.pack(pady=10)
    tk.Button(bottom, text="Empty / clear", command=lambda: choose(None)).pack(side="left", padx=6)
    tk.Button(bottom, text="Cancel", command=lambda: choose(PICK_CANCEL)).pack(side="left", padx=6)
    win.protocol("WM_DELETE_WINDOW", lambda: choose(PICK_CANCEL))
    win.grab_set()
    win.wait_window()
    return result["v"]


def start_position():
    back = "RNBQKBNR"
    board = [[None] * 8 for _ in range(8)]
    for c in range(8):
        board[0][c] = "b" + back[c]
        board[1][c] = "bP"
        board[6][c] = "wP"
        board[7][c] = "w" + back[c]
    return board


# --------------------------------------------------------------------------- #
#  Board model (thread-safe)
# --------------------------------------------------------------------------- #
class BoardModel:
    def __init__(self):
        self._lock = threading.Lock()
        self.board = start_position()
        self.last_move = None

    def reset(self):
        with self._lock:
            self.board = start_position()
            self.last_move = None

    def clear(self):
        with self._lock:
            self.board = [[None] * 8 for _ in range(8)]
            self.last_move = None

    def set_piece(self, row, col, code):
        """Place `code` ('wP'.. or None) on a square — for setting up positions."""
        with self._lock:
            self.board[row][col] = code

    def snapshot(self):
        with self._lock:
            return [row[:] for row in self.board], self.last_move

    def encode_arduino(self):
        with self._lock:
            vals = []
            for row in range(8):
                for col in range(8):
                    cell = self.board[row][col]
                    if cell is None:
                        vals.append(0)
                    else:
                        bit = 1 if cell[0] == "w" else 0
                        vals.append((TYPE_ID[cell[1]] << 1) | bit)
        return " ".join(str(v) for v in vals) + "\r\n"

    # -- classify a GUI move into the 6-byte protocol ----------------------- #
    def classify_move(self, frm, to, promo_letter="Q"):
        """Return (packet_tuple, description) for a move, WITHOUT applying it."""
        with self._lock:
            fr, fc = frm
            tr, tc = to
            piece = self.board[fr][fc]
            captured = self.board[tr][tc]
        if piece is None:
            return None, "no piece on source square"

        ptype = piece[1]
        color = piece[0]
        mtype = 1
        promo = 0
        desc = f"{sq_name(*frm)}->{sq_name(*to)}"

        # castling: king two files sideways
        if ptype == "K" and abs(tc - fc) == 2:
            mtype = 4 if tc > fc else 5
            packet = (mtype, 1, 1, 1, 1, 0)
            return packet, f"{MOVE_TYPE_NAME[mtype]}"

        # en-passant: pawn changes file onto an empty square
        if ptype == "P" and fc != tc and captured is None:
            mtype = 3
            desc += " (en-passant)"
        elif captured is not None:
            mtype = 2
            desc += f" (capture {captured})"

        # promotion: pawn reaches the far rank
        if ptype == "P" and ((color == "w" and tr == 0) or (color == "b" and tr == 7)):
            promo = PROMO_CODE.get(promo_letter, 1)
            desc += f" (promote {promo_letter})"

        packet = (mtype, fc + 1, 8 - fr, tc + 1, 8 - tr, promo)
        return packet, desc

    # -- apply a move locally (handles castle / ep / promotion) ------------- #
    def apply_move(self, frm, to, promo_letter="Q"):
        with self._lock:
            fr, fc = frm
            tr, tc = to
            piece = self.board[fr][fc]
            if piece is None:
                return
            color = piece[0]

            # castling
            if piece[1] == "K" and abs(tc - fc) == 2:
                self.board[tr][tc] = piece
                self.board[fr][fc] = None
                if tc > fc:                       # kingside: rook h->f
                    self.board[fr][5] = self.board[fr][7]
                    self.board[fr][7] = None
                else:                             # queenside: rook a->d
                    self.board[fr][3] = self.board[fr][0]
                    self.board[fr][0] = None
                self.last_move = (frm, to)
                return

            # en-passant capture
            if piece[1] == "P" and fc != tc and self.board[tr][tc] is None:
                self.board[fr][tc] = None         # remove passed pawn

            self.board[tr][tc] = piece
            self.board[fr][fc] = None

            # promotion
            if piece[1] == "P" and ((color == "w" and tr == 0) or (color == "b" and tr == 7)):
                self.board[tr][tc] = color + (promo_letter if promo_letter in PROMO_CODE else "Q")

            self.last_move = (frm, to)

    # -- apply an incoming robot packet (CLIENT mode) ----------------------- #
    def apply_robot_packet(self, mt, fx, fy, tx, ty, promo):
        with self._lock:
            if mt in (4, 5):
                if mt == 4:
                    self._raw((0, 4), (0, 6)); self._raw((0, 7), (0, 5))
                    self.last_move = ((0, 4), (0, 6))
                    return "black castles kingside"
                self._raw((0, 4), (0, 2)); self._raw((0, 0), (0, 3))
                self.last_move = ((0, 4), (0, 2))
                return "black castles queenside"

            frm = (8 - fy, fx - 1)
            to  = (8 - ty, tx - 1)
            fr, fc = frm
            tr, tc = to
            if not (0 <= fr < 8 and 0 <= fc < 8 and 0 <= tr < 8 and 0 <= tc < 8):
                return f"!! out-of-range ({fx},{fy})->({tx},{ty})"
            captured = self.board[tr][tc]
            piece = self.board[fr][fc]
            self._raw(frm, to)
            desc = f"{sq_name(*frm)}->{sq_name(*to)}"
            if mt == 3:
                self.board[fr][tc] = None
                desc += " (en-passant)"
            elif captured is not None:
                desc += f" (captured {captured})"
            if promo in PROMO_FROM_CODE and piece is not None:
                self.board[tr][tc] = piece[0] + PROMO_FROM_CODE[promo]
                desc += f" (promote {PROMO_FROM_CODE[promo]})"
            self.last_move = (frm, to)
            return desc

    def _raw(self, frm, to):
        fr, fc = frm
        tr, tc = to
        self.board[tr][tc] = self.board[fr][fc]
        self.board[fr][fc] = None


# --------------------------------------------------------------------------- #
#  Board servers
# --------------------------------------------------------------------------- #
class BoardSerialServer(threading.Thread):
    """Serves the Arduino protocol on a real / virtual COM port."""
    def __init__(self, model, port, baud, ui):
        super().__init__(daemon=True)
        self.model, self.port, self.baud, self.ui = model, port, baud, ui
        self._running = True
        self.ser = None

    def run(self):
        if serial is None:
            self.ui.put(("board_status", ("error", "pyserial not installed")))
            return
        try:
            self.ser = serial.Serial(self.port, self.baud, timeout=0.4)
        except Exception as e:
            self.ui.put(("board_status", ("error", f"{self.port}: {e}")))
            return
        self.ui.put(("board_status", ("ok", f"Arduino serving on {self.port}")))
        self.ui.put(("log", ("COM", f"board ready on {self.port} @ {self.baud}")))
        buf = bytearray()
        while self._running:
            try:
                data = self.ser.read(64)
            except Exception as e:
                self.ui.put(("board_status", ("error", str(e)))); break
            if not data:
                continue
            buf.extend(data)
            if b"a" in buf:
                buf.clear()
                self._respond(self.ser.write)
        try:
            if self.ser and self.ser.is_open:
                self.ser.close()
        except Exception:
            pass
        self.ui.put(("board_status", ("off", "stopped")))

    def _respond(self, write):
        line = self.model.encode_arduino().encode("ascii")
        try:
            write(line)
            self.ser.flush()
            self.ui.put(("log", ("COM", "scan 'a' -> sent 64-cell board state")))
        except Exception as e:
            self.ui.put(("board_status", ("error", str(e))))

    def stop(self):
        self._running = False


class BoardTcpServer(threading.Thread):
    """Serves the Arduino protocol over TCP (no driver needed)."""
    def __init__(self, model, host, port, ui):
        super().__init__(daemon=True)
        self.model, self.host, self.port, self.ui = model, host, port, ui
        self._running = True
        self.srv = None

    def run(self):
        try:
            self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.srv.bind((self.host, self.port))
            self.srv.listen(1)
            self.srv.settimeout(0.5)
        except Exception as e:
            self.ui.put(("board_status", ("error", str(e)))); return
        self.ui.put(("board_status", ("ok", f"board (TCP) on {self.host}:{self.port}")))
        self.ui.put(("log", ("COM", f"TCP board server on {self.host}:{self.port}")))
        while self._running:
            try:
                conn, addr = self.srv.accept()
            except socket.timeout:
                continue
            except Exception:
                break
            self.ui.put(("log", ("COM", f"board scanner connected: {addr}")))
            with conn:
                conn.settimeout(0.5)
                buf = bytearray()
                while self._running:
                    try:
                        data = conn.recv(64)
                    except socket.timeout:
                        continue
                    except Exception:
                        break
                    if not data:
                        break
                    buf.extend(data)
                    if b"a" in buf or b"SCAN" in buf:
                        buf.clear()
                        try:
                            conn.sendall(self.model.encode_arduino().encode("ascii"))
                            self.ui.put(("log", ("COM", "scan -> sent 64-cell board state")))
                        except Exception:
                            break
        try:
            self.srv.close()
        except Exception:
            pass
        self.ui.put(("board_status", ("off", "stopped")))

    def stop(self):
        self._running = False


# --------------------------------------------------------------------------- #
#  Robot socket – SERVER mode (robot connects to us)
# --------------------------------------------------------------------------- #
class RobotServer(threading.Thread):
    def __init__(self, host, port, ui):
        super().__init__(daemon=True)
        self.host, self.port, self.ui = host, port, ui
        self._running = True
        self.srv = None
        self.client = None
        self._lock = threading.Lock()

    def run(self):
        try:
            self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.srv.bind((self.host, self.port))
            self.srv.listen(1)
            self.srv.settimeout(0.5)
        except Exception as e:
            self.ui.put(("robot_status", ("error", str(e)))); return
        self.ui.put(("robot_status", ("ok", f"hosting robot on {self.host}:{self.port}")))
        self.ui.put(("log", ("UR3", f"robot server up on {self.host}:{self.port} "
                                     f"(robot should dial {HINT_ROBOT_IP}:{self.port})")))
        while self._running:
            try:
                conn, addr = self.srv.accept()
            except socket.timeout:
                continue
            except Exception:
                break
            with self._lock:
                self.client = conn
            self.ui.put(("robot_status", ("ok", f"robot connected: {addr}")))
            self.ui.put(("log", ("UR3", f"robot connected from {addr}")))
            conn.settimeout(0.5)
            while self._running:
                try:
                    data = conn.recv(64)
                except socket.timeout:
                    continue
                except Exception:
                    break
                if not data:
                    break
                self.ui.put(("log", ("UR3", f"robot says: {data!r}")))
            with self._lock:
                self.client = None
            self.ui.put(("log", ("UR3", "robot disconnected")))
            self.ui.put(("robot_status", ("ok", "waiting for robot...")))
        try:
            self.srv.close()
        except Exception:
            pass
        self.ui.put(("robot_status", ("off", "stopped")))

    def send_packet(self, packet):
        with self._lock:
            conn = self.client
        if not conn:
            self.ui.put(("log", ("UR3", "no robot connected – packet not sent")))
            return False
        try:
            conn.sendall(struct.pack("6B", *packet))
            raw = ".".join(str(b) for b in packet)
            self.ui.put(("log", ("UR3", f"-> sent packet ({raw}) "
                                         f"[{MOVE_TYPE_NAME.get(packet[0], '?')}]")))
            return True
        except Exception as e:
            self.ui.put(("robot_status", ("error", str(e))))
            return False

    def stop(self):
        self._running = False


# --------------------------------------------------------------------------- #
#  Robot socket – CLIENT mode (we connect to chess_main as the robot)
# --------------------------------------------------------------------------- #
class RobotClient(threading.Thread):
    def __init__(self, model, host, port, ui):
        super().__init__(daemon=True)
        self.model, self.host, self.port, self.ui = model, host, port, ui
        self._running = True
        self.sock = None
        self._lock = threading.Lock()

    def run(self):
        try:
            self.sock = socket.create_connection((self.host, self.port), timeout=5)
            self.sock.settimeout(0.5)
        except Exception as e:
            self.ui.put(("robot_status", ("error", f"{self.host}:{self.port}: {e}"))); return
        self.ui.put(("robot_status", ("ok", f"connected to brain {self.host}:{self.port}")))
        self.ui.put(("log", ("UR3", f"robot connected to brain {self.host}:{self.port}")))
        buf = bytearray()
        while self._running:
            try:
                data = self.sock.recv(64)
            except socket.timeout:
                continue
            except Exception as e:
                self.ui.put(("robot_status", ("error", str(e)))); break
            if not data:
                self.ui.put(("log", ("UR3", "brain closed connection"))); break
            buf.extend(data)
            while len(buf) >= 6:
                self._apply(bytes(buf[:6])); del buf[:6]
        try:
            self.sock.close()
        except Exception:
            pass
        self.ui.put(("robot_status", ("off", "disconnected")))

    def _apply(self, packet):
        vals = struct.unpack("6B", packet)
        desc = self.model.apply_robot_packet(*vals)
        raw = ".".join(str(b) for b in vals)
        self.ui.put(("log", ("UR3", f"<- packet ({raw}) [{MOVE_TYPE_NAME.get(vals[0], '?')}]")))
        self.ui.put(("log", ("UR3", f"   robot moves figure: {desc}")))
        self.ui.put(("redraw", None))

    def send_move_request(self):
        if not self.sock:
            self.ui.put(("log", ("UR3", "cannot send MOVE – not connected"))); return
        try:
            with self._lock:
                self.sock.sendall(b"MOVE\n")
            self.ui.put(("log", ("UR3", "-> sent 'MOVE' (requesting board scan)")))
        except Exception as e:
            self.ui.put(("robot_status", ("error", str(e))))

    def stop(self):
        self._running = False


# --------------------------------------------------------------------------- #
#  GUI
# --------------------------------------------------------------------------- #
class EmulatorApp:
    def __init__(self, root, on_switch=None, switch_label="→ Checkers"):
        self.root = root
        self.on_switch = on_switch
        self.switch_label = switch_label
        root.title("Chess Board + UR3 Robot Emulator")
        root.configure(bg="#2B2B2B")
        root.resizable(False, False)

        self.model = BoardModel()
        self.ui_queue = queue.Queue()
        self.board_srv = None
        self.robot_srv = None
        self.robot_cli = None
        self.selected = None

        self._build_ui()
        self._draw_board()
        self.root.after(60, self._pump)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---- layout ----------------------------------------------------------- #
    def _build_ui(self):
        outer = tk.Frame(self.root, bg="#2B2B2B")
        outer.pack(padx=10, pady=10)

        left = tk.Frame(outer, bg="#2B2B2B")
        left.grid(row=0, column=0, sticky="n")
        self.canvas = tk.Canvas(left, width=BOARD_PX + 2 * MARGIN,
                                height=BOARD_PX + 2 * MARGIN,
                                bg="#2B2B2B", highlightthickness=0)
        self.canvas.pack()
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Button-3>", self._on_right_click)   # set any figure
        tk.Label(left, bg="#2B2B2B", fg="#AAAAAA", justify="left", font=("Segoe UI", 8),
                 text="Left-click a piece then its target to make a move.\n"
                      "RIGHT-click a square to set any figure on it.").pack(pady=(6, 0))

        right = tk.Frame(outer, bg="#2B2B2B")
        right.grid(row=0, column=1, sticky="n", padx=(12, 0))
        if self.on_switch:
            tk.Button(right, text=self.switch_label, command=self.on_switch
                      ).pack(anchor="e", pady=(0, 6))
        self._build_board_panel(right)
        self._build_robot_panel(right)
        self._build_actions(right)
        self._build_log(right)

    def _build_board_panel(self, parent):
        box = tk.LabelFrame(parent, text="Board  (Arduino)", bg="#3C3F41", fg="white",
                            font=("Segoe UI", 9, "bold"), padx=8, pady=6)
        box.pack(fill="x")
        self.board_transport = tk.StringVar(value="com")
        tk.Radiobutton(box, text="com0com / COM", variable=self.board_transport, value="com",
                       bg="#3C3F41", fg="white", selectcolor="#222",
                       activebackground="#3C3F41", command=self._refresh_board_inputs
                       ).grid(row=0, column=0, sticky="w")
        tk.Radiobutton(box, text="TCP bridge", variable=self.board_transport, value="tcp",
                       bg="#3C3F41", fg="white", selectcolor="#222",
                       activebackground="#3C3F41", command=self._refresh_board_inputs
                       ).grid(row=0, column=1, sticky="w")

        tk.Label(box, text="Port:", bg="#3C3F41", fg="white").grid(row=1, column=0, sticky="w")
        self.board_port = tk.StringVar(value=com0com_setup.EMU_PORT_DEFAULT)
        self.board_port_cb = ttk.Combobox(box, textvariable=self.board_port, width=14,
                                          values=self._ports())
        self.board_port_cb.grid(row=1, column=1, padx=4, sticky="w")

        tk.Button(box, text="Setup com0com", command=self._setup_com0com).grid(
            row=1, column=2, padx=2)
        self.board_btn = tk.Button(box, text="Start", width=7, command=self._toggle_board)
        self.board_btn.grid(row=2, column=2, padx=2, pady=(4, 0))
        self.board_lamp = tk.Label(box, text="●", fg="#888", bg="#3C3F41")
        self.board_lamp.grid(row=2, column=0, sticky="e")
        self.board_status = tk.Label(box, text="board idle", bg="#3C3F41", fg="#AAA",
                                     font=("Segoe UI", 8), wraplength=240, justify="left")
        self.board_status.grid(row=3, column=0, columnspan=3, sticky="w", pady=(2, 0))

    def _build_robot_panel(self, parent):
        box = tk.LabelFrame(parent, text="Robot socket", bg="#3C3F41", fg="white",
                            font=("Segoe UI", 9, "bold"), padx=8, pady=6)
        box.pack(fill="x", pady=(8, 0))
        self.robot_mode = tk.StringVar(value="server")
        tk.Radiobutton(box, text="Server (robot connects to us)", variable=self.robot_mode,
                       value="server", bg="#3C3F41", fg="white", selectcolor="#222",
                       activebackground="#3C3F41").grid(row=0, column=0, columnspan=3, sticky="w")
        tk.Radiobutton(box, text="Client (connect to chess_main)", variable=self.robot_mode,
                       value="client", bg="#3C3F41", fg="white", selectcolor="#222",
                       activebackground="#3C3F41").grid(row=1, column=0, columnspan=3, sticky="w")

        tk.Label(box, text="Host:", bg="#3C3F41", fg="white").grid(row=2, column=0, sticky="w")
        self.robot_host = tk.StringVar(value=DEFAULT_ROBOT_HOST)
        tk.Entry(box, textvariable=self.robot_host, width=14).grid(row=2, column=1, padx=4)
        self.robot_port = tk.StringVar(value=str(DEFAULT_ROBOT_PORT))
        tk.Entry(box, textvariable=self.robot_port, width=6).grid(row=2, column=2, sticky="w")
        self.robot_btn = tk.Button(box, text="Start", width=7, command=self._toggle_robot)
        self.robot_btn.grid(row=3, column=2, padx=2, pady=(4, 0))
        self.robot_lamp = tk.Label(box, text="●", fg="#888", bg="#3C3F41")
        self.robot_lamp.grid(row=3, column=0, sticky="e")
        self.robot_status = tk.Label(box, text="robot idle", bg="#3C3F41", fg="#AAA",
                                     font=("Segoe UI", 8), wraplength=240, justify="left")
        self.robot_status.grid(row=4, column=0, columnspan=3, sticky="w", pady=(2, 0))

    def _build_actions(self, parent):
        box = tk.Frame(parent, bg="#2B2B2B")
        box.pack(fill="x", pady=8)
        tk.Label(box, text="Promote to:", bg="#2B2B2B", fg="white").pack(side="left")
        self.promo = tk.StringVar(value="Q")
        ttk.Combobox(box, textvariable=self.promo, width=3, state="readonly",
                     values=["Q", "R", "B", "N"]).pack(side="left", padx=4)
        self.move_btn = tk.Button(box, text="Send MOVE →", bg="#4C9A2A", fg="white",
                                  font=("Segoe UI", 9, "bold"), command=self._send_move_request)
        self.move_btn.pack(side="left", padx=8)
        tk.Button(box, text="Reset board", command=self._reset).pack(side="right")

    def _build_log(self, parent):
        box = tk.LabelFrame(parent, text="UR3 / COM command log", bg="#3C3F41", fg="white",
                            font=("Segoe UI", 9, "bold"), padx=4, pady=4)
        box.pack(fill="both", expand=True, pady=(4, 0))
        self.log = tk.Text(box, width=48, height=14, bg="#1E1E1E", fg="#D4D4D4",
                           font=("Consolas", 9), wrap="word", state="disabled")
        self.log.pack(side="left", fill="both", expand=True)
        sb = tk.Scrollbar(box, command=self.log.yview); sb.pack(side="right", fill="y")
        self.log.config(yscrollcommand=sb.set)
        self.log.tag_config("UR3", foreground="#6AAFFF")
        self.log.tag_config("COM", foreground="#C8E69B")
        self.log.tag_config("SYS", foreground="#FFCC66")
        tk.Button(parent, text="Clear log", command=self._clear_log).pack(anchor="e", pady=(2, 0))

    # ---- drawing ---------------------------------------------------------- #
    def _draw_board(self):
        c = self.canvas
        c.delete("all")
        board, last = self.model.snapshot()
        for row in range(8):
            for col in range(8):
                x0 = MARGIN + col * SQUARE
                y0 = MARGIN + row * SQUARE
                light = (row + col) % 2 == 0
                fill = COLOR_LIGHT if light else COLOR_DARK
                if last and ((row, col) == last[0] or (row, col) == last[1]):
                    fill = COLOR_LASTMOVE_L if light else COLOR_LASTMOVE_D
                if self.selected == (row, col):
                    fill = COLOR_SELECT
                c.create_rectangle(x0, y0, x0 + SQUARE, y0 + SQUARE, fill=fill, outline=fill)
                piece = board[row][col]
                if piece:
                    c.create_text(x0 + SQUARE / 2, y0 + SQUARE / 2, text=GLYPH[piece],
                                  font=("Segoe UI Symbol", 38),
                                  fill="#000000" if piece[0] == "b" else "#FFFFFF")
        for col in range(8):
            c.create_text(MARGIN + col * SQUARE + SQUARE / 2, MARGIN + BOARD_PX + 10,
                          text=FILES[col], fill=COLOR_LABEL, font=("Segoe UI", 9))
        for row in range(8):
            c.create_text(MARGIN - 10, MARGIN + row * SQUARE + SQUARE / 2,
                          text=RANKS[row], fill=COLOR_LABEL, font=("Segoe UI", 9))

    def _cell_at(self, x, y):
        col = (x - MARGIN) // SQUARE
        row = (y - MARGIN) // SQUARE
        if 0 <= row < 8 and 0 <= col < 8:
            return int(row), int(col)
        return None

    # ---- interactions ----------------------------------------------------- #
    def _on_click(self, event):
        cell = self._cell_at(event.x, event.y)
        if cell is None:
            return
        board, _ = self.model.snapshot()
        row, col = cell
        if self.selected is None:
            if board[row][col] is not None:
                self.selected = cell
                self._draw_board()
        else:
            if cell == self.selected:
                self.selected = None
            else:
                self._make_move(self.selected, cell)
                self.selected = None
            self._draw_board()

    def _on_right_click(self, event):
        """Right-click a square -> dialog to set any figure on it."""
        cell = self._cell_at(event.x, event.y)
        if cell is None:
            return
        row, col = cell
        rows = [
            [(GLYPH[c], c, "#000") for c in ("wK", "wQ", "wR", "wB", "wN", "wP")],
            [(GLYPH[c], c, "#000") for c in ("bK", "bQ", "bR", "bB", "bN", "bP")],
        ]
        choice = piece_picker(self.root, sq_name(row, col), rows)
        if choice == PICK_CANCEL:
            return
        self.model.set_piece(row, col, choice)
        self._log("SYS", f"set {sq_name(row, col)} = {choice or 'empty'}")
        self.selected = None
        self._draw_board()

    def _make_move(self, frm, to):
        promo = self.promo.get()
        packet, desc = self.model.classify_move(frm, to, promo)
        self.model.apply_move(frm, to, promo)
        self._log("SYS", f"move {desc}")
        # Server mode -> push the move to the connected robot.
        if self.robot_srv and packet:
            self.robot_srv.send_packet(packet)
        # Client mode -> a board change means the human moved; trigger a scan.
        elif self.robot_cli:
            self.robot_cli.send_move_request()

    def _send_move_request(self):
        if self.robot_cli:
            self.robot_cli.send_move_request()
        else:
            self._log("SYS", "Send MOVE only applies in CLIENT mode")

    def _reset(self):
        self.model.reset(); self.selected = None
        self._draw_board(); self._log("SYS", "board reset to start position")

    # ---- board connection ------------------------------------------------- #
    def _refresh_board_inputs(self):
        if self.board_transport.get() == "tcp":
            self.board_port.set(f"{DEFAULT_TCP_BOARD[0]}:{DEFAULT_TCP_BOARD[1]}")
        else:
            self.board_port_cb.config(values=self._ports())
            self.board_port.set(com0com_setup.EMU_PORT_DEFAULT)

    def _setup_com0com(self):
        info = com0com_setup.ensure_pair()
        self._log("SYS", info["message"])
        if info["ok"]:
            self.board_transport.set("com")
            self.board_port.set(info["emu"])
            self.board_port_cb.config(values=self._ports())
            messagebox.showinfo("com0com", info["message"])
        else:
            messagebox.showwarning("com0com", info["message"])

    def _toggle_board(self):
        if self.board_srv and self.board_srv.is_alive():
            self.board_srv.stop(); self.board_srv = None
            self.board_btn.config(text="Start"); return
        if self.board_transport.get() == "tcp":
            target = self.board_port.get().strip()
            host, _, port = target.partition(":")
            self.board_srv = BoardTcpServer(self.model, host or "0.0.0.0",
                                            int(port or DEFAULT_TCP_BOARD[1]), self.ui_queue)
        else:
            self.board_srv = BoardSerialServer(self.model, self.board_port.get().strip(),
                                               DEFAULT_BAUD, self.ui_queue)
        self.board_srv.start()
        self.board_btn.config(text="Stop")

    # ---- robot connection ------------------------------------------------- #
    def _toggle_robot(self):
        running = (self.robot_srv and self.robot_srv.is_alive()) or \
                  (self.robot_cli and self.robot_cli.is_alive())
        if running:
            if self.robot_srv:
                self.robot_srv.stop(); self.robot_srv = None
            if self.robot_cli:
                self.robot_cli.stop(); self.robot_cli = None
            self.robot_btn.config(text="Start"); return
        try:
            port = int(self.robot_port.get().strip())
        except ValueError:
            self._log("SYS", "invalid robot port"); return
        host = self.robot_host.get().strip()
        if self.robot_mode.get() == "server":
            self.robot_srv = RobotServer(host, port, self.ui_queue); self.robot_srv.start()
        else:
            self.robot_cli = RobotClient(self.model, host, port, self.ui_queue); self.robot_cli.start()
        self.robot_btn.config(text="Stop")

    # ---- ui queue --------------------------------------------------------- #
    def _pump(self):
        try:
            while True:
                kind, payload = self.ui_queue.get_nowait()
                if kind == "log":
                    self._log(payload[0], payload[1])
                elif kind == "redraw":
                    self._draw_board()
                elif kind == "board_status":
                    self._status(self.board_lamp, self.board_status, *payload)
                    if payload[0] in ("off", "error"):
                        self.board_btn.config(text="Start")
                elif kind == "robot_status":
                    self._status(self.robot_lamp, self.robot_status, *payload)
                    if payload[0] in ("off", "error"):
                        self.robot_btn.config(text="Start")
        except queue.Empty:
            pass
        self.root.after(60, self._pump)

    def _status(self, lamp, label, state, text):
        colors = {"ok": "#4CAF50", "error": "#E05050", "off": "#888"}
        lamp.config(fg=colors.get(state, "#888"))
        label.config(text=text)

    # ---- log -------------------------------------------------------------- #
    def _log(self, tag, msg):
        self.log.config(state="normal")
        self.log.insert("end", msg + "\n", tag)
        self.log.see("end")
        self.log.config(state="disabled")

    def _clear_log(self):
        self.log.config(state="normal")
        self.log.delete("1.0", "end")
        self.log.config(state="disabled")

    # ---- misc ------------------------------------------------------------- #
    def _ports(self):
        ports = []
        if list_ports is not None:
            ports = [p.device for p in list_ports.comports()]
        for _, com in com0com_setup.list_pairs():
            if com and com != "COM#" and com not in ports:
                ports.append(com)          # com0com ports (registry, not in Ports class)
        if com0com_setup.EMU_PORT_DEFAULT not in ports:
            ports.append(com0com_setup.EMU_PORT_DEFAULT)
        return ports

    def shutdown(self):
        for srv in (self.board_srv, self.robot_srv, self.robot_cli):
            if srv:
                srv.stop()

    def _on_close(self):
        self.shutdown()
        self.root.destroy()


# --------------------------------------------------------------------------- #
#  Mode switching — one program, Chess or Checkers
# --------------------------------------------------------------------------- #
def _launch(root, mode, holder):
    """Tear down the current app and build the chosen mode on the same root."""
    app = holder.get("app")
    if app is not None and hasattr(app, "shutdown"):
        app.shutdown()
    for child in root.winfo_children():
        child.destroy()

    if mode == "chess":
        holder["app"] = EmulatorApp(
            root, on_switch=lambda: _launch(root, "checkers", holder),
            switch_label="⇄ Checkers")
    else:
        import checkers_emulator as ce
        holder["app"] = ce.CheckersEmulatorApp(
            root, on_switch=lambda: _launch(root, "chess", holder),
            switch_label="⇄ Chess")


def _chooser(root, holder):
    root.title("Emulator — choose mode")
    root.configure(bg="#2B2B2B")
    frame = tk.Frame(root, bg="#2B2B2B")
    frame.pack(padx=40, pady=36)
    tk.Label(frame, text="Board / Robot Emulator", bg="#2B2B2B", fg="white",
             font=("Segoe UI", 15, "bold")).pack(pady=(0, 16))
    tk.Label(frame, text="Which game do you want to emulate?", bg="#2B2B2B",
             fg="#CCC", font=("Segoe UI", 10)).pack(pady=(0, 14))
    tk.Button(frame, text="♛  Chess", width=22, height=2, font=("Segoe UI", 11, "bold"),
              command=lambda: _launch(root, "chess", holder)).pack(pady=4)
    tk.Button(frame, text="⛀  Checkers", width=22, height=2, font=("Segoe UI", 11, "bold"),
              command=lambda: _launch(root, "checkers", holder)).pack(pady=4)


def main():
    root = tk.Tk()
    holder = {}
    _chooser(root, holder)
    root.mainloop()


if __name__ == "__main__":
    main()
