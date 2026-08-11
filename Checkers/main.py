import copy
import functools
import os
import platform
import socket
import sys
import threading
import time
from typing import Optional

# Run from this file's folder so relative assets (res/, settings) always resolve.
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ── High-DPI awareness (Windows) — MUST run before any Tk window is created ───
def apply_dpi_awareness(enable: bool = True):
    if not enable or sys.platform != "win32":
        return
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)   # per-monitor v2
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()        # system DPI aware
    except Exception:
        pass


# Read persisted settings early so DPI awareness can be set before Tk starts.
import settings_store
SETTINGS = settings_store.load()
apply_dpi_awareness(SETTINGS.get("dpi_aware", True))

# If the operator picked the TCP bridge, expose it to board_tcp_serial via env.
if SETTINGS.get("connection_mode") == "tcp":
    os.environ["BOARD_TCP"] = SETTINGS.get("tcp_target", "127.0.0.1:5006")
else:
    os.environ.pop("BOARD_TCP", None)

import board_tcp_serial   # serial.Serial or a TCP bridge (BOARD_TCP env)
from tkinter import *
from tkinter import ttk
from tkinter import messagebox

from pyrsistent import freeze
from PIL import Image, ImageTk

from checkersanalyser.common import simplified_board, _get_winning_side
from checkersanalyser.model.board import Board
from checkersanalyser.model.completemove import CompleteMove
from checkersanalyser.model.sides import BLACKES, WHITES
from checkersanalyser.moveanalyser import MoveAnalyser
from checkersanalyser.predrag.movemaker import get_best_move

# ── Board / network configuration (populated from SETTINGS at startup) ────────
# The board comes from an ARDUINO over a COM port (or a TCP bridge). The game
# writes 'a' and reads back 64 space-separated ints (index = row*8 + col):
#   0 = empty, 1 = white piece, 3 = black piece.
CONFIG = {
    "serial_port": SETTINGS["serial_port"],
    "baud": SETTINGS["baud"],
}

ROBOT_HOST = SETTINGS["robot_host"]   # we host the socket; the robot connects to us
ROBOT_PORT = int(SETTINGS["robot_port"])

winner_name_map = {WHITES: "Player", BLACKES: "Computer"}

# ── UI palette / fonts (theme is chosen in settings) ──────────────────────────
THEMES = {
    "Wood": {
        "BG": "#462e25", "PANEL_BG": "#5a3d31", "CARD_BG": "#efe7dd",
        "ACCENT": "#D2B48C", "TXT_LIGHT": "#f3ece4", "CARD_FG": "#3a2a20",
        "SQ_LIGHT": "#E5D3B3", "SQ_DARK": "#5a3218",
        "WHITE_FG": "#1b3a6b", "BLACK_FG": "#7a1f1f", "TOOLBAR": "#3a251d",
    },
    "Dark": {
        "BG": "#1b1d23", "PANEL_BG": "#23262e", "CARD_BG": "#2c313c",
        "ACCENT": "#d2b48c", "TXT_LIGHT": "#f3ece4", "CARD_FG": "#e6e6e6",
        "SQ_LIGHT": "#6b7280", "SQ_DARK": "#2f333b",
        "WHITE_FG": "#8ab4f8", "BLACK_FG": "#f28b82", "TOOLBAR": "#14161b",
    },
    "Slate": {
        "BG": "#2b3a42", "PANEL_BG": "#34474f", "CARD_BG": "#e8eef0",
        "ACCENT": "#7fb3c9", "TXT_LIGHT": "#eef4f6", "CARD_FG": "#22323a",
        "SQ_LIGHT": "#d7e2e6", "SQ_DARK": "#3d5560",
        "WHITE_FG": "#1b4f6b", "BLACK_FG": "#7a1f1f", "TOOLBAR": "#22323a",
    },
}
THEME = THEMES.get(SETTINGS.get("theme", "Wood"), THEMES["Wood"])

BG        = THEME["BG"]
PANEL_BG  = THEME["PANEL_BG"]
CARD_BG   = THEME["CARD_BG"]
ACCENT    = THEME["ACCENT"]
TXT_LIGHT = THEME["TXT_LIGHT"]
FONT      = ("Segoe UI", 11)
FONT_B    = ("Segoe UI", 11, "bold")
FONT_H    = ("Segoe UI", 13, "bold")
FONT_MONO = ("Consolas", 11)
PAD       = 8


def synchronized(wrapped):
    lock = threading.Lock()

    @functools.wraps(wrapped)
    def _wrap(*args, **kwargs):
        with lock:
            return wrapped(*args, **kwargs)

    return _wrap


# ── Errors ───────────────────────────────────────────────────────────────────
class BoardError(Exception):
    """Raised when the Arduino board / emulator can't be read or sent bad data."""


CameraError = BoardError      # backwards-compatible alias


def _beep():
    if platform.system() == "Windows":
        try:
            import winsound
            winsound.Beep(440, 300)
        except Exception:
            pass


def show_error(title, problem, advice):
    """Error dialog that says WHAT is wrong and WHAT TO DO about it."""
    _beep()
    if isinstance(advice, (list, tuple)):
        advice = "\n".join(f"  •  {a}" for a in advice)
    messagebox.showerror(title, f"{problem}\n\nWhat to do:\n{advice}")


def error_msg():
    show_error(
        "Move Prediction Error",
        "Failed to deduce the player's move from the board.",
        [
            "Make sure exactly one legal move was made on the physical board.",
            "Check the pieces are clearly placed on their squares.",
            "Press 'Refresh Memory Board' to re-sync, then try again.",
        ],
    )


# ── Notation / scoring helpers ────────────────────────────────────────────────
def cell_name(pos) -> str:
    """(x, y) -> algebraic square like 'c3' (file a-h, rank 1-8 bottom-up)."""
    x, y = pos
    return f"{'abcdefgh'[x]}{8 - y}"


def move_notation(complete_move: CompleteMove) -> str:
    """Readable move text, e.g. 'c3-d4' or a capture chain 'c3xe5xg7'."""
    moves = complete_move.moves
    text = cell_name(moves[0].fr)
    for m in moves:
        text += ("x" if m.is_eat_move else "-") + cell_name(m.to)
    return text


def count_pieces(board) -> tuple[int, int]:
    """Return (white_count, black_count) on the board."""
    white = sum(1 for row in board for c in row if c in (1, 2))
    black = sum(1 for row in board for c in row if c in (3, 4))
    return white, black


def standard_start() -> list[list[int]]:
    """Standard 12-vs-12 checkers starting position (black top, white bottom)."""
    board = [[0 for _ in range(8)] for _ in range(8)]
    for y in range(8):
        for x in range(8):
            if (x + y) % 2 == 1:        # dark (playable) squares
                if y <= 2:
                    board[y][x] = 3     # black pawns
                elif y >= 5:
                    board[y][x] = 1     # white pawns
    return board


def predict_player_move(board) -> Optional[list[CompleteMove]]:
    print("Predicting player move")
    attempts = 10
    while attempts > 0:
        new_board = get_board_from_camera()
        player_moves = MoveAnalyser(board, new_board).calculate_move_for_side(WHITES)
        if len(player_moves) == 0:
            attempts -= 1
            continue
        return player_moves
    print("ERROR: Failed to deduce player move")
    return None


class RobotClient:
    """
    TCP server the UR3 robot connects to. Robust against disconnects: it keeps a
    background accept loop running so the robot can reconnect at any time, and it
    reports connection changes and inbound 'MOVE' requests through callbacks.
    """

    def __init__(self, host=ROBOT_HOST, port=ROBOT_PORT,
                 on_status=None, on_move_request=None):
        self.host = host
        self.port = port
        self.on_status = on_status
        self.on_move_request = on_move_request
        self._lock = threading.Lock()
        self._conn = None
        self._running = True

        self.s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.s.bind((self.host, self.port))
        self.s.listen(10)

        threading.Thread(target=self._accept_loop, daemon=True).start()

    # -- connection lifecycle ---------------------------------------------------
    def _accept_loop(self):
        while self._running:
            try:
                conn, addr = self.s.accept()
            except OSError:
                if not self._running:
                    break
                time.sleep(0.5)
                continue
            with self._lock:
                self._conn = conn
            print(f"[ROBOT] connected from {addr}")
            self._notify_status(True)
            self._watch(conn)

    def _watch(self, conn):
        """Hold the connection, detect close, and forward 'MOVE' requests."""
        conn.settimeout(1.0)
        while self._running:
            try:
                data = conn.recv(4)
                if not data:
                    break
                if data.strip() == b"MOVE" and self.on_move_request:
                    self.on_move_request()
            except socket.timeout:
                continue
            except OSError:
                break
        with self._lock:
            if self._conn is conn:
                self._conn = None
        try:
            conn.close()
        except Exception:
            pass
        print("[ROBOT] disconnected")
        self._notify_status(False)

    def _notify_status(self, ok):
        if self.on_status:
            try:
                self.on_status(ok)
            except Exception:
                pass

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._conn is not None

    def close(self):
        self._running = False
        try:
            self.s.close()
        except Exception:
            pass
        with self._lock:
            if self._conn:
                try:
                    self._conn.close()
                except Exception:
                    pass

    # -- sending moves ----------------------------------------------------------
    def _send(self, data: bytes):
        with self._lock:
            conn = self._conn
        if conn is None:
            raise RuntimeError("robot is not connected")
        conn.sendall(data)

    def make_move(self, complete_move: CompleteMove):
        self.move_figure(*complete_move.moves[0].fr, 1)
        [self.move_figure(*m.fr, 2) for m in complete_move.moves[1:]]
        if any([i.is_eat_move for i in complete_move.moves]):
            self.move_figure(*complete_move.moves[-1].to, 5)
        else:
            self.move_figure(*complete_move.moves[-1].to, 3)
        [self.move_figure(*m.get_eaten_cell(), 4)
         for m in complete_move.moves if m.get_eaten_cell() is not None]

    def move_figure(self, x, y, command):
        y = 7 - y
        self._send((chr(x) + chr(y) + chr(command)).encode('utf8'))


def get_images(dim: int) -> list:
    image_scale = (dim, dim)
    i1 = ImageTk.PhotoImage(Image.open("res/1b.gif").resize(image_scale))
    i2 = ImageTk.PhotoImage(Image.open("res/1bk.gif").resize(image_scale))
    i3 = ImageTk.PhotoImage(Image.open("res/1h.gif").resize(image_scale))
    i4 = ImageTk.PhotoImage(Image.open("res/1hk.gif").resize(image_scale))
    return [0, i1, i2, i3, i4]


def create_move(board) -> Optional[CompleteMove]:
    print("Starting to deduce AI move")
    board_clone = Board(board)
    return get_best_move(board_clone, BLACKES, randomized=True)


class ArduinoBoard:
    """Reads the 8x8 board from the Arduino over a COM port (or the TCP bridge)."""

    def __init__(self):
        self._lock = threading.Lock()
        self.ser = None
        self.port = CONFIG["serial_port"]

    def open(self):
        with self._lock:
            self._open_locked()

    def _open_locked(self):
        self._close_locked()
        self.port = CONFIG["serial_port"]
        self.ser = board_tcp_serial.open_board(self.port, CONFIG["baud"], timeout=2)

    def _close_locked(self):
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass
        self.ser = None

    @property
    def connected(self):
        return bool(self.ser and getattr(self.ser, "is_open", False))

    def read_board(self) -> list[list[int]]:
        """Scan the board: write 'a', read 64 ints, return an 8x8 of 0/1/3."""
        with self._lock:
            if self.ser is None:
                try:
                    self._open_locked()
                except Exception as e:
                    raise BoardError(f"Cannot open board port {self.port}  ({e})")
            try:
                self.ser.reset_input_buffer()
                self.ser.write(b'a\n')
                self.ser.flush()
                line = self.ser.readline()
                if len(line) < 16:           # skip an echo / short line
                    line = self.ser.readline()
            except Exception as e:
                self._close_locked()
                raise BoardError(f"Board read failed on {self.port}  ({e})")
        text = line.decode('ascii', errors='ignore').strip()
        try:
            vals = [int(x) for x in text.split() if x]
        except ValueError:
            raise BoardError(f"Board on {self.port} sent unparsable data: {text!r}")
        if len(vals) < 64:
            raise BoardError(f"Board on {self.port} returned {len(vals)} values, expected 64.")
        board = [[0] * 8 for _ in range(8)]
        for i in range(64):
            v = vals[i]
            board[i // 8][i % 8] = 1 if v in (1, 2) else 3 if v in (3, 4) else 0
        return board


_BOARD_READER = ArduinoBoard()


@synchronized
def get_board_from_camera() -> list[list[int]]:
    """Kept name for compatibility; now reads the Arduino board over the COM port."""
    return _BOARD_READER.read_board()


class CheckersGameWindow:

    def __init__(self, dim=None):
        self.gl_okno = Tk()
        self.gl_okno.title('Шашки — Checkers / UR3   (F11 full-screen)')
        self.gl_okno.config(bg=BG)
        self.gl_okno.minsize(900, 600)

        # Apply UI scaling (fonts / widget metrics) from settings.
        try:
            self.gl_okno.tk.call("tk", "scaling", float(SETTINGS.get("ui_scale", 1.0)))
        except Exception:
            pass

        self.dim = int(dim if dim is not None else SETTINGS.get("cell_size", 80))
        self.images = get_images(self.dim)
        self.fullscreen = False
        self._last_memory_board = standard_start()

        # command hooks wired by CheckersGame
        self.on_undo = None
        self.on_redo = None
        self.on_reconnect_board = None

        self._build_menu()
        self._build_toolbar()
        self._build_body()
        self._build_statusbar()
        self._bind_keys()

        if SETTINGS.get("start_fullscreen"):
            self.gl_okno.after(100, self.enter_fullscreen)

    # ── menu bar ──────────────────────────────────────────────────────────────
    def _build_menu(self):
        menubar = Menu(self.gl_okno)

        game = Menu(menubar, tearoff=0)
        game.add_command(label="New / Reset Board", accelerator="Ctrl+N",
                         command=lambda: self._call("reset"))
        game.add_command(label="Refresh Memory Board", accelerator="F5",
                         command=lambda: self._call("refresh"))
        game.add_separator()
        game.add_command(label="Undo", accelerator="Ctrl+Z",
                         command=lambda: self.on_undo and self.on_undo())
        game.add_command(label="Redo", accelerator="Ctrl+Y",
                         command=lambda: self.on_redo and self.on_redo())
        game.add_separator()
        game.add_command(label="Exit", accelerator="Alt+F4",
                         command=self.gl_okno.destroy)
        menubar.add_cascade(label="Game", menu=game)

        view = Menu(menubar, tearoff=0)
        view.add_command(label="Zoom In", accelerator="Ctrl++",
                         command=lambda: self.zoom(+8))
        view.add_command(label="Zoom Out", accelerator="Ctrl+-",
                         command=lambda: self.zoom(-8))
        view.add_separator()
        view.add_command(label="Toggle Full-screen", accelerator="F11",
                         command=self.toggle_fullscreen)
        menubar.add_cascade(label="View", menu=view)

        robot = Menu(menubar, tearoff=0)
        robot.add_command(label="Reconnect Board",
                          command=lambda: self.on_reconnect_board and self.on_reconnect_board())
        menubar.add_cascade(label="Connection", menu=robot)

        helpm = Menu(menubar, tearoff=0)
        helpm.add_command(label="Controls", command=self._show_controls)
        helpm.add_command(label="About", command=self._show_about)
        menubar.add_cascade(label="Help", menu=helpm)

        self.gl_okno.config(menu=menubar)
        self._menu_cmds = {}

    def _call(self, name):
        cb = self._menu_cmds.get(name)
        if cb:
            cb()

    # ── toolbar ───────────────────────────────────────────────────────────────
    def _build_toolbar(self):
        bar = Frame(self.gl_okno, bg=THEME["TOOLBAR"])
        bar.grid(row=0, column=0, columnspan=3, sticky="ew")
        bar.grid_columnconfigure(20, weight=1)

        def tb(text, cmd, col, tip=None, bg=ACCENT, fg="#20232a"):
            b = Button(bar, text=text, command=cmd, bg=bg, fg=fg, relief="flat",
                       font=FONT_B, padx=10, pady=4, cursor="hand2",
                       activebackground="#e6cfa8")
            b.grid(row=0, column=col, padx=3, pady=5)
            return b

        self._btn_move = tb("Robot Move", lambda: None, 0)
        self._btn_refresh = tb("Refresh", lambda: None, 1, bg="#c9b18a")
        self.btn_undo = tb("Undo", lambda: self.on_undo and self.on_undo(), 2, bg="#c9b18a")
        self.btn_redo = tb("Redo", lambda: self.on_redo and self.on_redo(), 3, bg="#c9b18a")
        self._btn_reset = tb("Reset", lambda: None, 4, bg="#c98b6a", fg="white")
        tb("Full-screen", self.toggle_fullscreen, 5, bg="#8a7357", fg="white")

        # right-aligned live connection lamps
        lamps = Frame(bar, bg=THEME["TOOLBAR"])
        lamps.grid(row=0, column=21, sticky="e", padx=(0, 10))
        self.lamp_board = Label(lamps, text="● Board", bg=THEME["TOOLBAR"],
                                fg="#888", font=FONT_B)
        self.lamp_board.pack(side=LEFT, padx=6)
        self.lamp_robot = Label(lamps, text="● Robot", bg=THEME["TOOLBAR"],
                                fg="#888", font=FONT_B)
        self.lamp_robot.pack(side=LEFT, padx=6)

    # ── main body (boards + info) ─────────────────────────────────────────────
    def _build_body(self):
        board_px = self.dim * 8

        header = Frame(self.gl_okno, bg=BG)
        header.grid(row=1, column=0, columnspan=3, sticky="ew", padx=PAD, pady=(PAD, 0))
        header.grid_columnconfigure(0, minsize=board_px)
        header.grid_columnconfigure(1, minsize=board_px)
        self._hdr_cam = Label(header, text="Sensor Board (Arduino)", bg=BG,
                              fg=TXT_LIGHT, font=FONT_H)
        self._hdr_cam.grid(row=0, column=0)
        self._hdr_mem = Label(header, text="Memory Board", bg=BG, fg=TXT_LIGHT, font=FONT_H)
        self._hdr_mem.grid(row=0, column=1)
        Label(header, text="Game Info", bg=BG, fg=TXT_LIGHT, font=FONT_H
              ).grid(row=0, column=2, padx=(PAD, 0))

        self.camera_board_canvas = Canvas(self.gl_okno, width=board_px, height=board_px,
                                          bg=THEME["SQ_LIGHT"], borderwidth=0,
                                          highlightthickness=0)
        self.camera_board_canvas.grid(row=2, column=0, padx=PAD, pady=PAD)
        self.memory_board_canvas = Canvas(self.gl_okno, width=board_px, height=board_px,
                                          bg=THEME["SQ_LIGHT"], borderwidth=0,
                                          highlightthickness=0)
        self.memory_board_canvas.grid(row=2, column=1, padx=PAD, pady=PAD)

        self._build_info_panel(row=2, column=2, height=board_px)

        # Convenience handles (commands wired by CheckersGame)
        self.button1 = self._btn_move
        self.button2 = self._btn_refresh

    # -- info panel -------------------------------------------------------------
    def _build_info_panel(self, row, column, height):
        panel = Frame(self.gl_okno, bg=BG, width=340)
        panel.grid(row=row, column=column, sticky="ns", padx=(0, PAD), pady=PAD)
        panel.grid_propagate(False)
        panel.configure(height=height)

        def card(title):
            lf = LabelFrame(panel, text=title, bg=CARD_BG, fg=THEME["CARD_FG"],
                            font=FONT_B, padx=8, pady=6)
            lf.pack(fill="x", pady=(0, PAD))
            return lf

        # Score
        score = card("Score")
        self.score_white = Label(score, text="Player (White):  12   captured 0",
                                 bg=CARD_BG, fg=THEME["WHITE_FG"], font=FONT, anchor="w")
        self.score_white.pack(fill="x")
        self.score_black = Label(score, text="Computer (Black):  12   captured 0",
                                 bg=CARD_BG, fg=THEME["BLACK_FG"], font=FONT, anchor="w")
        self.score_black.pack(fill="x")
        self.turn_label = Label(score, text="Turn:  Player (White)", bg=CARD_BG,
                                fg=THEME["CARD_FG"], font=FONT_B, anchor="w")
        self.turn_label.pack(fill="x", pady=(4, 0))

        # Connection
        conn = card("Connection")
        iprow = Frame(conn, bg=CARD_BG); iprow.pack(fill="x")
        Label(iprow, text="Board COM:", bg=CARD_BG, font=FONT).pack(side=LEFT)
        self.ip_var = StringVar(value=CONFIG["serial_port"])
        Entry(iprow, textvariable=self.ip_var, width=10, font=FONT).pack(side=LEFT, padx=4)
        Button(iprow, text="Apply", font=FONT, command=self._apply_ip).pack(side=LEFT)
        self.cam_status = Label(conn, text="● Board: unknown", bg=CARD_BG,
                                fg="#888", font=FONT, anchor="w")
        self.cam_status.pack(fill="x", pady=(4, 0))
        self.robot_status = Label(conn, text="● Robot: waiting", bg=CARD_BG,
                                  fg="#888", font=FONT, anchor="w")
        self.robot_status.pack(fill="x")

        # Move history
        hist = card("Move History")
        hrow = Frame(hist, bg=CARD_BG); hrow.pack(fill="both", expand=True)
        self.history = Listbox(hrow, height=10, font=FONT_MONO, activestyle="none",
                               bg="#fbf7f1", fg="#2a2018", borderwidth=0,
                               highlightthickness=1, highlightbackground="#cdbfae")
        sb = Scrollbar(hrow, command=self.history.yview)
        self.history.configure(yscrollcommand=sb.set)
        self.history.pack(side=LEFT, fill="both", expand=True)
        sb.pack(side=RIGHT, fill="y")

        # Buttons
        btns = Frame(panel, bg=BG); btns.pack(fill="x")
        row1 = Frame(btns, bg=BG); row1.pack(fill="x", pady=(0, 6))
        self.p_btn_undo = Button(row1, text="Undo", bg=ACCENT, font=FONT,
                                 command=lambda: self.on_undo and self.on_undo())
        self.p_btn_undo.pack(side=LEFT, expand=True, fill="x", padx=(0, 3))
        self.p_btn_redo = Button(row1, text="Redo", bg=ACCENT, font=FONT,
                                 command=lambda: self.on_redo and self.on_redo())
        self.p_btn_redo.pack(side=LEFT, expand=True, fill="x", padx=(3, 0))

    # -- IP / status / score / history accessors --------------------------------
    def _apply_ip(self):
        CONFIG["serial_port"] = self.ip_var.get().strip() or "COM20"
        self.ip_var.set(CONFIG["serial_port"])
        try:
            _BOARD_READER.open()          # reopen on the new port
            self.set_status(f"Board port set to {CONFIG['serial_port']}", "ok")
        except Exception as e:
            self.set_status(f"Could not open {CONFIG['serial_port']}: {e}", "err")
        print(f"[CONFIG] board serial_port -> {CONFIG['serial_port']}")

    def set_camera_status(self, ok):
        self.cam_status.configure(
            text=f"● Board: {'connected' if ok else 'disconnected'}",
            fg="#2e9e40" if ok else "#c0392b")
        self.lamp_board.configure(fg="#3ad35a" if ok else "#e05555")

    def set_robot_status(self, ok):
        self.robot_status.configure(
            text=f"● Robot: {'connected' if ok else 'waiting'}",
            fg="#2e9e40" if ok else "#b07410")
        self.lamp_robot.configure(fg="#3ad35a" if ok else "#e0a355")

    def set_score(self, white, black):
        self.score_white.configure(text=f"Player (White):  {white}   captured {12 - black}")
        self.score_black.configure(text=f"Computer (Black):  {black}   captured {12 - white}")

    def set_turn(self, text):
        self.turn_label.configure(text=f"Turn:  {text}")

    def add_history(self, text, side):
        self.history.insert(END, text)
        self.history.itemconfig(END, fg=THEME["WHITE_FG"] if side == WHITES else THEME["BLACK_FG"])
        self.history.see(END)

    def clear_history(self):
        self.history.delete(0, END)

    def rebuild_history(self, entries):
        self.history.delete(0, END)
        for text, side in entries:
            self.add_history(text, side)

    def set_undo_redo_state(self, can_undo, can_redo):
        for b in (self.btn_undo, self.p_btn_undo):
            b.configure(state=NORMAL if can_undo else DISABLED)
        for b in (self.btn_redo, self.p_btn_redo):
            b.configure(state=NORMAL if can_redo else DISABLED)

    # ── status bar ────────────────────────────────────────────────────────────
    def _build_statusbar(self):
        bar = Frame(self.gl_okno, bg=THEME["TOOLBAR"])
        bar.grid(row=3, column=0, columnspan=3, sticky="ew")
        self.status_label = Label(bar, text="Ready.", bg=THEME["TOOLBAR"],
                                  fg=TXT_LIGHT, font=FONT, anchor="w")
        self.status_label.pack(side=LEFT, padx=10, pady=3)
        self.zoom_label = Label(bar, text=f"{self.dim}px", bg=THEME["TOOLBAR"],
                                fg=TXT_LIGHT, font=FONT, anchor="e")
        self.zoom_label.pack(side=RIGHT, padx=10)

    def set_status(self, text, kind="info"):
        colors = {"info": TXT_LIGHT, "ok": "#5fd67a", "warn": "#e0b84a", "err": "#ff6b6b"}
        self.status_label.configure(text=text, fg=colors.get(kind, TXT_LIGHT))

    # ── keyboard / fullscreen / zoom ──────────────────────────────────────────
    def _bind_keys(self):
        w = self.gl_okno
        w.bind("<F11>", self.toggle_fullscreen)
        w.bind("<Escape>", lambda e: self.exit_fullscreen())
        w.bind("<F5>", lambda e: self._call("refresh"))
        w.bind("<Control-z>", lambda e: self.on_undo and self.on_undo())
        w.bind("<Control-y>", lambda e: self.on_redo and self.on_redo())
        w.bind("<Control-n>", lambda e: self._call("reset"))
        w.bind("<Control-plus>", lambda e: self.zoom(+8))
        w.bind("<Control-equal>", lambda e: self.zoom(+8))
        w.bind("<Control-minus>", lambda e: self.zoom(-8))

    def toggle_fullscreen(self, event=None):
        self.enter_fullscreen() if not self.fullscreen else self.exit_fullscreen()

    def enter_fullscreen(self):
        self.fullscreen = True
        try:
            self.gl_okno.attributes("-fullscreen", True)
        except Exception:
            self.gl_okno.state("zoomed")
        self.set_status("Full-screen — press F11 or Esc to exit.", "info")

    def exit_fullscreen(self):
        if not self.fullscreen:
            return
        self.fullscreen = False
        try:
            self.gl_okno.attributes("-fullscreen", False)
        except Exception:
            pass
        self.set_status("Windowed.", "info")

    def zoom(self, delta):
        self.set_dim(self.dim + delta)

    def set_dim(self, dim):
        dim = max(40, min(160, int(dim)))
        if dim == self.dim:
            return
        self.dim = dim
        self.images = get_images(dim)
        board_px = dim * 8
        self.camera_board_canvas.config(width=board_px, height=board_px)
        self.memory_board_canvas.config(width=board_px, height=board_px)
        self.zoom_label.configure(text=f"{dim}px")
        self.render_memory_board(self._last_memory_board)
        try:
            self.render_camera_board()
        except CameraError:
            pass

    # -- board rendering --------------------------------------------------------
    def render_camera_board(self):
        camera_board = get_board_from_camera()
        self._render_board(self.camera_board_canvas, camera_board)

    def render_memory_board(self, memory_board):
        self._last_memory_board = memory_board
        self._render_board(self.memory_board_canvas, memory_board)

    def _render_board(self, canvas: Canvas, board: list[list[int]]):
        dim = self.dim
        canvas.delete('all')
        v1xs = [i * dim * 2 + dim for i in range(4)]
        v2xs = [i * dim * 2 for i in range(4)]
        for ri in range(8):
            xs = v1xs if ri % 2 == 0 else v2xs
            [canvas.create_rectangle(i, ri * dim, i + dim, ri * dim + dim,
                                     fill=THEME["SQ_DARK"], width=0) for i in xs]

        for i in range(64):
            x = i // 8
            y = i % 8
            z = board[y][x]
            if z:
                canvas.create_image(x * dim, y * dim, anchor=NW, image=self.images[z])

    def update_view(self, on_status=None):
        """Periodic camera refresh; resilient to a disconnected camera/emulator."""
        ok = True
        try:
            self.render_camera_board()
        except CameraError:
            ok = False
        self.set_camera_status(ok)
        if on_status:
            on_status(ok)
        # poll fast when connected, slowly when not (keeps the UI responsive)
        delay = 1000 if ok else 3000
        self.gl_okno.after(delay, lambda: self.update_view(on_status))

    # ── help dialogs ──────────────────────────────────────────────────────────
    def _show_controls(self):
        messagebox.showinfo(
            "Controls",
            "Robot Move ............ compute & send the computer's move\n"
            "Refresh (F5) .......... re-read the physical board\n"
            "Undo (Ctrl+Z) ......... step back one move\n"
            "Redo (Ctrl+Y) ......... replay an undone move\n"
            "Reset (Ctrl+N) ........ new standard game\n"
            "Zoom (Ctrl +/-) ....... resize the boards\n"
            "Full-screen (F11) ..... toggle; Esc exits\n\n"
            "Click a piece on the Memory Board to toggle pawn/queen "
            "if the sensor board read it wrong.")

    def _show_about(self):
        messagebox.showinfo(
            "About",
            "Checkers — Robotic Board\n\n"
            "Play draughts against the computer on a physical Arduino-sensed "
            "board with a UR3 robot moving the pieces.\n\n"
            f"Board port: {CONFIG['serial_port']}  •  Robot port: {ROBOT_PORT}")


class CheckersGame:

    def __init__(self, window: CheckersGameWindow):
        self.window = window
        self.history: list[CompleteMove] = []
        self.move_no = 0
        self.hist_ui: list[tuple[str, object]] = []
        self.side_to_move = WHITES
        self._undo_stack: list[dict] = []
        self._redo_stack: list[dict] = []
        self._board_was_ok = None
        self._robot_was_ok = None

        # Board: try the Arduino, else fall back to a standard start so the UI loads.
        try:
            self.board = get_board_from_camera()
            self.window.set_camera_status(True)
        except BoardError as e:
            self.board = standard_start()
            self.window.set_camera_status(False)
            show_error(
                "Board not available",
                str(e),
                [
                    "Start the board emulator (Checkers mode) and start its board server.",
                    f"Confirm the COM port (currently {CONFIG['serial_port']}) — it must be the "
                    f"paired end of the emulator's port (com0com).",
                    "Or choose the TCP bridge in the setup window.",
                    "The game has loaded with a standard starting position for now.",
                ],
            )

        # Robot server (we host; the robot connects to us).
        try:
            self.robot_client = RobotClient(
                host=ROBOT_HOST, port=ROBOT_PORT,
                on_status=self._on_robot_status,
                on_move_request=self._on_robot_move_request)
        except OSError as e:
            self.robot_client = None
            show_error(
                "Robot port unavailable",
                f"Could not open the robot socket on port {ROBOT_PORT}  ({e}).",
                [
                    "Close any other instance of this program already running.",
                    f"Make sure nothing else is using TCP port {ROBOT_PORT}.",
                    "Restart the program once the port is free.",
                ],
            )

        # Wire UI commands
        self.window.button1.configure(command=self.computer_make_move)
        self.window.button2.configure(command=self.refresh_memory_board)
        self.window._btn_reset.configure(command=self.reset_board)
        self.window.on_undo = self.undo
        self.window.on_redo = self.redo
        self.window.on_reconnect_board = self.reconnect_board
        self.window._menu_cmds = {"reset": self.reset_board,
                                  "refresh": self.refresh_memory_board}
        self.window.memory_board_canvas.bind("<Button-1>", self.mem_board_click_handle)
        self.window.gl_okno.protocol("WM_DELETE_WINDOW", self._on_close)

        self._refresh_score_and_turn()
        self._update_undo_redo_buttons()

    # -- robot status callbacks (called from the robot thread) ------------------
    def _on_robot_status(self, ok):
        def apply():
            self.window.set_robot_status(ok)
            if self._robot_was_ok is not None and ok != self._robot_was_ok:
                if ok:
                    self.window.set_status("Robot connected.", "ok")
                else:
                    self.window.set_status("Robot disconnected — check the UR3 power/network.", "warn")
            self._robot_was_ok = ok
        self.window.gl_okno.after(0, apply)

    def _on_robot_move_request(self):
        # The robot asked us to make the computer move; do it on the UI thread.
        self.window.gl_okno.after(0, self.computer_make_move)

    def _on_close(self):
        if self.robot_client:
            self.robot_client.close()
        self.window.gl_okno.destroy()

    # -- undo / redo ------------------------------------------------------------
    def _snapshot(self) -> dict:
        return {
            "board": copy.deepcopy(self.board),
            "move_no": self.move_no,
            "history": list(self.history),
            "hist_ui": list(self.hist_ui),
            "side_to_move": self.side_to_move,
        }

    def _restore(self, snap: dict):
        self.board = copy.deepcopy(snap["board"])
        self.move_no = snap["move_no"]
        self.history = list(snap["history"])
        self.hist_ui = list(snap["hist_ui"])
        self.side_to_move = snap["side_to_move"]
        self.window.rebuild_history(self.hist_ui)
        self.window.render_memory_board(self.board)
        self._refresh_score_and_turn(self.side_to_move)
        self._update_undo_redo_buttons()

    def _push_undo(self):
        self._undo_stack.append(self._snapshot())
        self._redo_stack.clear()
        self._update_undo_redo_buttons()

    def _update_undo_redo_buttons(self):
        self.window.set_undo_redo_state(bool(self._undo_stack), bool(self._redo_stack))

    def undo(self):
        if not self._undo_stack:
            self.window.set_status("Nothing to undo.", "warn")
            return
        self._redo_stack.append(self._snapshot())
        self._restore(self._undo_stack.pop())
        self.window.set_status("Undid last move.", "info")

    def redo(self):
        if not self._redo_stack:
            self.window.set_status("Nothing to redo.", "warn")
            return
        self._undo_stack.append(self._snapshot())
        self._restore(self._redo_stack.pop())
        self._update_undo_redo_buttons()
        self.window.set_status("Redid move.", "info")

    # -- helpers ----------------------------------------------------------------
    def _refresh_score_and_turn(self, side_to_move=WHITES):
        self.side_to_move = side_to_move
        white, black = count_pieces(self.board)
        self.window.set_score(white, black)
        self.window.set_turn(winner_name_map[side_to_move]
                             + (" (White)" if side_to_move == WHITES else " (Black)"))

    def _record_move(self, complete_move: CompleteMove):
        if complete_move.side == WHITES:
            self.move_no += 1
        marker = "W" if complete_move.side == WHITES else "B"
        prefix = f"{self.move_no:>2}. {marker}  "
        text = prefix + move_notation(complete_move)
        self.window.add_history(text, complete_move.side)
        self.hist_ui.append((text, complete_move.side))
        self.history.append(complete_move)

    # -- interactions -----------------------------------------------------------
    def mem_board_click_handle(self, event):
        dim = self.window.dim
        board = self.board
        cx, cy = event.x // dim, event.y // dim
        if not (0 <= cx < 8 and 0 <= cy < 8):
            return
        square = board[cy][cx]
        toggle = {1: 2, 2: 1, 3: 4, 4: 3}      # pawn <-> queen of same colour
        if square in toggle:
            self._push_undo()
            board[cy][cx] = toggle[square]
        self.window.render_memory_board(board)
        self._refresh_score_and_turn(self.side_to_move)

    def update_board(self, move: CompleteMove):
        print(f"Updating memory board with {move}")
        self._push_undo()
        try:
            self.board = Board(self.board).execute_complete_move(move).tolist()
        except Exception as e:
            self._undo_stack.pop()   # the move didn't happen; drop the snapshot
            self._update_undo_redo_buttons()
            show_error(
                "Could not apply move",
                f"The move {move_notation(move)} could not be applied to the board ({e}).",
                ["Press 'Refresh Memory Board' to re-sync from the board.",
                 "If it persists, use 'Reset Board' to start a fresh game."],
            )
            return
        self._record_move(move)
        self.window.render_memory_board(self.board)
        self._refresh_score_and_turn(BLACKES if move.side == WHITES else WHITES)

    def update_board_with_player_move(self, player_moves: list[CompleteMove]):
        player_move = player_moves[0]
        if len(player_moves) > 1:
            print("Multiple candidate player moves; using the first:", player_moves)
        print(f"Updating board with player move: {player_move}")
        self.update_board(player_move)

    def refresh_memory_board(self):
        try:
            self.board = get_board_from_camera()
        except BoardError as e:
            self.window.set_camera_status(False)
            self.window.set_status("Board read failed.", "err")
            show_error("Board not available", str(e),
                       ["Start the board emulator (Checkers mode) and its board server.",
                        f"Check the COM port ({CONFIG['serial_port']}) or use the TCP bridge."])
            return
        self.window.set_camera_status(True)
        self.board[WHITES.upgrade_line()] = [WHITES.to_queen(c) for c in self.board[WHITES.upgrade_line()]]
        self.board[BLACKES.upgrade_line()] = [BLACKES.to_queen(c) for c in self.board[BLACKES.upgrade_line()]]
        self.window.render_memory_board(self.board)
        self._refresh_score_and_turn()
        self.window.set_status("Memory board re-synced from the sensor board.", "ok")

    def reconnect_board(self):
        try:
            _BOARD_READER.open()
            self.window.set_camera_status(True)
            self.window.set_status(f"Reconnected to board on {CONFIG['serial_port']}.", "ok")
        except Exception as e:
            self.window.set_camera_status(False)
            show_error("Reconnect failed",
                       f"Could not reopen the board on {CONFIG['serial_port']}  ({e}).",
                       ["Check the cable / emulator is running.",
                        "Confirm the COM port in the Connection panel."])

    def reset_board(self):
        if not messagebox.askyesno("Reset Board",
                                   "Reset to the standard starting position and clear the move history?"):
            return
        self._push_undo()
        self.board = standard_start()
        self.history.clear()
        self.move_no = 0
        self.hist_ui.clear()
        self.window.clear_history()
        self.window.render_memory_board(self.board)
        self._refresh_score_and_turn(WHITES)
        self.window.set_status("Board reset to the standard starting position.", "ok")
        print("[RESET] Board reset to standard starting position.")

    def computer_make_move(self):
        self.window.button1['state'] = DISABLED
        try:
            self._do_computer_make_move()
        finally:
            self.window.button1['state'] = NORMAL

    def _do_computer_make_move(self):
        side = _get_winning_side(Board(self.board))
        if side is not None:
            messagebox.showinfo("Game Over", f"Winner is {winner_name_map[side]}.")
            return

        try:
            camera_board = get_board_from_camera()
            self.window.set_camera_status(True)
        except BoardError as e:
            self.window.set_camera_status(False)
            self.window.set_status("Board read failed.", "err")
            show_error("Board not available", str(e),
                       ["Start the board emulator (Checkers mode) and its board server, then try again.",
                        f"Check the COM port ({CONFIG['serial_port']}) or use the TCP bridge."])
            return

        # If the player hasn't changed anything, just let the robot move.
        s_board = simplified_board(freeze(self.board))
        if s_board == camera_board:
            self.do_robot_move()
            return

        player_moves = predict_player_move(self.board)
        if player_moves is None:
            error_msg()
            return
        self.update_board_with_player_move(player_moves)
        self.do_robot_move()

    def do_robot_move(self):
        move = create_move(self.board)
        if move is None:
            messagebox.showinfo("Game Over", "Winner is Player.")
            return

        if self.robot_client is not None and self.robot_client.connected:
            try:
                self.robot_client.make_move(move)
                self.window.set_robot_status(True)
                self.window.set_status("Move sent to the robot.", "ok")
            except (OSError, RuntimeError) as e:
                self.window.set_robot_status(False)
                show_error("Robot communication failed",
                           f"Could not send the move to the robot ({e}).",
                           ["Check the UR3 robot is powered and connected.",
                            f"Confirm the robot connected to this PC on port {ROBOT_PORT}.",
                            "The move was still applied to the memory board."])
        else:
            self.window.set_status("Robot not connected — move applied to memory only.", "warn")
            show_error("Robot not connected",
                       "No robot is connected, so the move was not sent to the robot arm.",
                       [f"Make sure the UR3 connects to this PC on port {ROBOT_PORT}.",
                        "Use Connection ▸ (lamp) to watch the robot status.",
                        "The move was still applied to the memory board."])

        self.update_board(move)

    def start(self):
        self.window.update_view(on_status=self._on_board_status)
        self.window.render_memory_board(self.board)
        self.window.set_status("Ready. Make a move on the physical board, then press Robot Move.", "info")
        self.window.gl_okno.mainloop()

    def _on_board_status(self, ok):
        if self._board_was_ok is not None and ok != self._board_was_ok:
            if ok:
                self.window.set_status("Board reconnected.", "ok")
            else:
                self.window.set_status("Board disconnected — check the COM port / emulator.", "warn")
        self._board_was_ok = ok


def main():
    # Show the setup window first; it saves the chosen settings to disk.
    try:
        import settings_dialog
        chosen = settings_dialog.ask_settings(SETTINGS)
    except Exception as e:
        print(f"[SETUP] settings window failed ({e}); using saved/default settings.")
        chosen = SETTINGS
    if chosen is None:
        print("Setup cancelled — exiting.")
        return

    # Apply the freshly chosen settings to this run's globals.
    global CONFIG, ROBOT_HOST, ROBOT_PORT, THEME, BG, PANEL_BG, CARD_BG, ACCENT, TXT_LIGHT
    SETTINGS.update(chosen)
    CONFIG["serial_port"] = SETTINGS["serial_port"]
    CONFIG["baud"] = SETTINGS["baud"]
    ROBOT_HOST = SETTINGS["robot_host"]
    ROBOT_PORT = int(SETTINGS["robot_port"])
    if SETTINGS.get("connection_mode") == "tcp":
        os.environ["BOARD_TCP"] = SETTINGS.get("tcp_target", "127.0.0.1:5006")
    else:
        os.environ.pop("BOARD_TCP", None)

    THEME = THEMES.get(SETTINGS.get("theme", "Wood"), THEMES["Wood"])
    BG, PANEL_BG, CARD_BG = THEME["BG"], THEME["PANEL_BG"], THEME["CARD_BG"]
    ACCENT, TXT_LIGHT = THEME["ACCENT"], THEME["TXT_LIGHT"]

    game = CheckersGame(CheckersGameWindow())
    game.start()


if __name__ == "__main__":
    main()
