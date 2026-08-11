# -*- coding: utf-8 -*-
"""
Checkers mode for the board/robot emulator
==========================================

Stands in for the *physical hardware* the checkers game (``..\\Checkers\\main.py``)
talks to, so it can run with no real Arduino board and no real robot.

  1. BOARD  (Arduino over a COM port — same idea as the chess board)
     The game scans the board by writing 'a'; we answer with 64 space-separated
     ints (index = row*8 + col):  0 = empty, 1 = white piece, 3 = black piece.
     Transport: com0com virtual COM pair (recommended) or a TCP bridge.
     We reuse the chess emulator's BoardSerialServer / BoardTcpServer because
     they are model-agnostic (they just call model.encode_arduino()).

  2. ROBOT  (TCP client -> the game's socket SERVER on port 3000)
     The game hosts the socket; the robot connects, sends "MOVE" to ask for a
     turn, and receives 3-byte move commands  chr(x) + chr(7 - y) + chr(command):
         1 = lift (start)   2 = via   3 = place   5 = place after eat
         4 = remove eaten piece
     We apply those to our board and visualise the robot moving the figure.

Closed loop: make a WHITE move on the board -> press "Send MOVE" -> the game
scans the board (us, over COM), deduces the move, computes Black's reply and
streams the 3-byte commands back -> we move the black piece on screen.

Piece codes:  0 empty | 1 white pawn | 2 white king | 3 black pawn | 4 black king
"""

import socket
import threading
import queue
import tkinter as tk
from tkinter import ttk, messagebox

from emulator import (BoardSerialServer, BoardTcpServer,   # model-agnostic board servers
                      piece_picker, PICK_CANCEL)
import com0com_setup

# ── Protocol constants ────────────────────────────────────────────────────────
ROBOT_PORT_DEFAULT = 3000
DEFAULT_HOST       = "127.0.0.1"
DEFAULT_TCP_BOARD  = ("127.0.0.1", 5006)
DEFAULT_BAUD       = 9600

CMD_LIFT, CMD_VIA, CMD_PLACE, CMD_REMOVE, CMD_PLACE_EAT = 1, 2, 3, 4, 5
CMD_NAME = {1: "LIFT", 2: "VIA", 3: "PLACE", 4: "REMOVE", 5: "PLACE(eat)"}

WHITE_CODES = (1, 2)
BLACK_CODES = (3, 4)

# ── Board geometry / colours ──────────────────────────────────────────────────
SQUARE = 64
MARGIN = 22
BOARD_PX = SQUARE * 8
C_LIGHT = "#E5D3B3"
C_DARK  = "#7a4a2b"
C_SEL   = "#F6F669"
C_LAST  = "#C9A66B"


def standard_start():
    """12-vs-12 checkers start: black (3) rows 0-2, white (1) rows 5-7, dark squares."""
    board = [[0] * 8 for _ in range(8)]
    for y in range(8):
        for x in range(8):
            if (x + y) % 2 == 1:
                if y <= 2:
                    board[y][x] = 3
                elif y >= 5:
                    board[y][x] = 1
    return board


# ── Board model ───────────────────────────────────────────────────────────────
class CheckersModel:
    """board[row][col]; row 0 top. Thread-safe."""

    def __init__(self):
        self._lock = threading.Lock()
        self.board = standard_start()
        self.last = None
        self._held = None

    def reset(self):
        with self._lock:
            self.board = standard_start()
            self.last = None
            self._held = None

    def clear(self):
        with self._lock:
            self.board = [[0] * 8 for _ in range(8)]
            self.last = None
            self._held = None

    def set_cell(self, row, col, val):
        with self._lock:
            self.board[row][col] = val

    def snapshot(self):
        with self._lock:
            return [row[:] for row in self.board], self.last

    def encode_arduino(self):
        """64 space-separated ints + CRLF; 0 empty / 1 white / 3 black (presence)."""
        with self._lock:
            vals = []
            for r in range(8):
                for c in range(8):
                    cell = self.board[r][c]
                    vals.append(1 if cell in WHITE_CODES else 3 if cell in BLACK_CODES else 0)
        return " ".join(str(v) for v in vals) + "\r\n"

    def human_move(self, frm, to):
        with self._lock:
            fr, fc = frm
            tr, tc = to
            piece = self.board[fr][fc]
            if piece == 0:
                return False
            self.board[fr][fc] = 0
            if piece == 1 and tr == 0:
                piece = 2
            elif piece == 3 and tr == 7:
                piece = 4
            self.board[tr][tc] = piece
            self.last = (frm, to)
            return True

    def apply_robot_command(self, b0, b1, b2):
        col = b0
        row = 7 - b1
        cmd = b2
        if not (0 <= row < 8 and 0 <= col < 8):
            return f"!! out-of-range cmd ({b0},{b1},{b2})"
        with self._lock:
            name = CMD_NAME.get(cmd, f"cmd{cmd}")
            if cmd in (CMD_LIFT, CMD_VIA):
                self._held = self.board[row][col] or self._held
                self.board[row][col] = 0
                self.last = ((row, col), self.last[1] if self.last else (row, col))
                return f"{name} ({col},{row})"
            if cmd in (CMD_PLACE, CMD_PLACE_EAT):
                piece = self._held or 3
                if piece == 3 and row == 7:
                    piece = 4
                elif piece == 1 and row == 0:
                    piece = 2
                self.board[row][col] = piece
                start = self.last[0] if self.last else (row, col)
                self.last = (start, (row, col))
                self._held = None
                return f"{name} -> ({col},{row})"
            if cmd == CMD_REMOVE:
                self.board[row][col] = 0
                return f"REMOVE eaten ({col},{row})"
            return f"{name} ({col},{row})"

    def counts(self):
        with self._lock:
            w = sum(1 for r in self.board for c in r if c in WHITE_CODES)
            b = sum(1 for r in self.board for c in r if c in BLACK_CODES)
        return w, b


# ── Robot client (connects to the game's server on :3000) ─────────────────────
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
            self.ui.put(("robot_status", ("error", f"{self.host}:{self.port}: {e}")))
            return
        self.ui.put(("robot_status", ("ok", f"connected to game {self.host}:{self.port}")))
        self.ui.put(("log", ("ROB", f"robot connected to game {self.host}:{self.port}")))
        buf = bytearray()
        while self._running:
            try:
                data = self.sock.recv(64)
            except socket.timeout:
                continue
            except Exception as e:
                self.ui.put(("robot_status", ("error", str(e))))
                break
            if not data:
                self.ui.put(("log", ("ROB", "game closed the connection")))
                break
            buf.extend(data)
            while len(buf) >= 3:
                b0, b1, b2 = buf[0], buf[1], buf[2]
                del buf[:3]
                desc = self.model.apply_robot_command(b0, b1, b2)
                self.ui.put(("log", ("ROB", f"<- {desc}")))
                self.ui.put(("redraw", None))
        try:
            self.sock.close()
        except Exception:
            pass
        self.ui.put(("robot_status", ("off", "disconnected")))

    def send_move_request(self):
        if not self.sock:
            self.ui.put(("log", ("ROB", "cannot send MOVE — not connected")))
            return
        try:
            with self._lock:
                self.sock.sendall(b"MOVE")
            self.ui.put(("log", ("ROB", "-> sent 'MOVE' (request a turn)")))
        except Exception as e:
            self.ui.put(("robot_status", ("error", str(e))))

    def stop(self):
        self._running = False


# ── GUI ────────────────────────────────────────────────────────────────────────
class CheckersEmulatorApp:
    def __init__(self, root, on_switch=None, switch_label="⇄ Chess"):
        self.root = root
        self.on_switch = on_switch
        root.title("Checkers — Arduino board + Robot Emulator")
        root.configure(bg="#2B2B2B")
        root.resizable(False, False)

        self.model = CheckersModel()
        self.ui_queue = queue.Queue()
        self.board_srv = None
        self.robot_cli = None
        self.selected = None

        self._build_ui(switch_label)
        self._draw_board()
        self.root.after(60, self._pump)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # -- layout --
    def _build_ui(self, switch_label):
        outer = tk.Frame(self.root, bg="#2B2B2B")
        outer.pack(padx=10, pady=10)

        left = tk.Frame(outer, bg="#2B2B2B")
        left.grid(row=0, column=0, sticky="n")
        self.canvas = tk.Canvas(left, width=BOARD_PX + 2 * MARGIN,
                                height=BOARD_PX + 2 * MARGIN, bg="#2B2B2B",
                                highlightthickness=0)
        self.canvas.pack()
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Button-3>", self._on_right_click)   # set any figure
        tk.Label(left, bg="#2B2B2B", fg="#AAAAAA", justify="left", font=("Segoe UI", 8),
                 text="Left-click a piece then its target to make a move.\n"
                      "RIGHT-click a square to set any figure on it."
                 ).pack(pady=(6, 0))

        right = tk.Frame(outer, bg="#2B2B2B")
        right.grid(row=0, column=1, sticky="n", padx=(12, 0))
        if self.on_switch:
            tk.Button(right, text=switch_label, command=self.on_switch
                      ).pack(anchor="e", pady=(0, 6))

        # --- Board (Arduino) panel ---
        box = tk.LabelFrame(right, text="Board  (Arduino)", bg="#3C3F41", fg="white",
                            font=("Segoe UI", 9, "bold"), padx=8, pady=6)
        box.pack(fill="x")
        self.transport = tk.StringVar(value="com")
        tk.Radiobutton(box, text="com0com / COM", variable=self.transport, value="com",
                       bg="#3C3F41", fg="white", selectcolor="#222",
                       command=self._refresh_inputs).grid(row=0, column=0, sticky="w")
        tk.Radiobutton(box, text="TCP bridge", variable=self.transport, value="tcp",
                       bg="#3C3F41", fg="white", selectcolor="#222",
                       command=self._refresh_inputs).grid(row=0, column=1, sticky="w")
        tk.Label(box, text="Port:", bg="#3C3F41", fg="white").grid(row=1, column=0, sticky="w")
        self.board_port = tk.StringVar(value=com0com_setup.EMU_PORT_DEFAULT)
        self.board_port_cb = ttk.Combobox(box, textvariable=self.board_port, width=14,
                                          values=self._ports())
        self.board_port_cb.grid(row=1, column=1, padx=4, sticky="w")
        tk.Button(box, text="Setup com0com", command=self._setup_com0com).grid(row=1, column=2, padx=2)
        self.board_btn = tk.Button(box, text="Start", width=7, command=self._toggle_board)
        self.board_btn.grid(row=2, column=2, padx=2, pady=(4, 0))
        self.board_lamp = tk.Label(box, text="●", fg="#888", bg="#3C3F41")
        self.board_lamp.grid(row=2, column=0, sticky="e")
        self.board_status = tk.Label(box, text="board idle", bg="#3C3F41", fg="#AAA",
                                     font=("Segoe UI", 8), wraplength=240, justify="left")
        self.board_status.grid(row=3, column=0, columnspan=3, sticky="w")

        # --- Robot panel ---
        rob = tk.LabelFrame(right, text="Robot", bg="#3C3F41", fg="white",
                            font=("Segoe UI", 9, "bold"), padx=8, pady=6)
        rob.pack(fill="x", pady=(8, 0))
        tk.Label(rob, text="Game host:", bg="#3C3F41", fg="white").grid(row=0, column=0, sticky="w")
        self.rob_host = tk.StringVar(value=DEFAULT_HOST)
        tk.Entry(rob, textvariable=self.rob_host, width=12).grid(row=0, column=1, padx=4)
        self.rob_port = tk.StringVar(value=str(ROBOT_PORT_DEFAULT))
        tk.Entry(rob, textvariable=self.rob_port, width=6).grid(row=0, column=2)
        self.rob_btn = tk.Button(rob, text="Connect", width=7, command=self._toggle_robot)
        self.rob_btn.grid(row=0, column=3, padx=4)
        self.rob_lamp = tk.Label(rob, text="●", fg="#888", bg="#3C3F41")
        self.rob_lamp.grid(row=1, column=3)
        self.rob_status = tk.Label(rob, text="robot idle", bg="#3C3F41", fg="#AAA",
                                   font=("Segoe UI", 8), wraplength=240, justify="left")
        self.rob_status.grid(row=1, column=0, columnspan=3, sticky="w")

        act = tk.Frame(right, bg="#2B2B2B")
        act.pack(fill="x", pady=8)
        self.score = tk.Label(act, text="White 12   Black 12", bg="#2B2B2B", fg="white",
                              font=("Segoe UI", 10, "bold"))
        self.score.pack(side="left")
        tk.Button(act, text="Reset", command=self._reset).pack(side="right")
        tk.Button(act, text="Send MOVE →", bg="#4C9A2A", fg="white",
                  font=("Segoe UI", 9, "bold"), command=self._send_move).pack(side="right", padx=6)

        box2 = tk.LabelFrame(right, text="Board / Robot log", bg="#3C3F41", fg="white",
                             font=("Segoe UI", 9, "bold"), padx=4, pady=4)
        box2.pack(fill="both", expand=True)
        self.log = tk.Text(box2, width=44, height=14, bg="#1E1E1E", fg="#D4D4D4",
                           font=("Consolas", 9), wrap="word", state="disabled")
        self.log.pack(side="left", fill="both", expand=True)
        sb = tk.Scrollbar(box2, command=self.log.yview); sb.pack(side="right", fill="y")
        self.log.config(yscrollcommand=sb.set)
        self.log.tag_config("ROB", foreground="#6AAFFF")
        self.log.tag_config("COM", foreground="#C8E69B")
        self.log.tag_config("SYS", foreground="#FFCC66")

    # -- board helpers --
    def _ports(self):
        ports = []
        try:
            import serial.tools.list_ports as lp
            ports = [p.device for p in lp.comports()]
        except Exception:
            pass
        for _, com in com0com_setup.list_pairs():
            if com and com != "COM#" and com not in ports:
                ports.append(com)
        if com0com_setup.EMU_PORT_DEFAULT not in ports:
            ports.append(com0com_setup.EMU_PORT_DEFAULT)
        return ports

    def _refresh_inputs(self):
        if self.transport.get() == "tcp":
            self.board_port.set(f"{DEFAULT_TCP_BOARD[0]}:{DEFAULT_TCP_BOARD[1]}")
        else:
            self.board_port_cb.config(values=self._ports())
            self.board_port.set(com0com_setup.EMU_PORT_DEFAULT)

    def _setup_com0com(self):
        info = com0com_setup.ensure_pair()
        self._log("SYS", info["message"])
        if info["ok"]:
            self.transport.set("com")
            self.board_port.set(info["emu"])
            self.board_port_cb.config(values=self._ports())
            messagebox.showinfo("com0com", info["message"])
        else:
            messagebox.showwarning("com0com", info["message"])

    def _toggle_board(self):
        if self.board_srv and self.board_srv.is_alive():
            self.board_srv.stop(); self.board_srv = None
            self.board_btn.config(text="Start"); return
        if self.transport.get() == "tcp":
            host, _, port = self.board_port.get().strip().partition(":")
            self.board_srv = BoardTcpServer(self.model, host or "127.0.0.1",
                                            int(port or DEFAULT_TCP_BOARD[1]), self.ui_queue)
        else:
            self.board_srv = BoardSerialServer(self.model, self.board_port.get().strip(),
                                               DEFAULT_BAUD, self.ui_queue)
        self.board_srv.start()
        self.board_btn.config(text="Stop")

    # -- drawing --
    def _draw_board(self):
        c = self.canvas
        c.delete("all")
        board, last = self.model.snapshot()
        for row in range(8):
            for col in range(8):
                x0 = MARGIN + col * SQUARE
                y0 = MARGIN + row * SQUARE
                dark = (row + col) % 2 == 1
                fill = C_DARK if dark else C_LIGHT
                if last and ((row, col) == last[0] or (row, col) == last[1]):
                    fill = C_LAST
                if self.selected == (row, col):
                    fill = C_SEL
                c.create_rectangle(x0, y0, x0 + SQUARE, y0 + SQUARE, fill=fill, outline=fill)
                piece = board[row][col]
                if piece:
                    self._draw_piece(c, x0, y0, piece)
        for col in range(8):
            c.create_text(MARGIN + col * SQUARE + SQUARE / 2, MARGIN + BOARD_PX + 9,
                          text="abcdefgh"[col], fill="#CCC", font=("Segoe UI", 9))
        for row in range(8):
            c.create_text(MARGIN - 9, MARGIN + row * SQUARE + SQUARE / 2,
                          text=str(8 - row), fill="#CCC", font=("Segoe UI", 9))
        w, b = self.model.counts()
        self.score.configure(text=f"White {w}   Black {b}")

    def _draw_piece(self, c, x0, y0, piece):
        pad = 8
        white = piece in WHITE_CODES
        king = piece in (2, 4)
        fill = "#f1e3c6" if white else "#2a2018"
        outline = "#8a6d3b" if white else "#000000"
        c.create_oval(x0 + pad, y0 + pad, x0 + SQUARE - pad, y0 + SQUARE - pad,
                      fill=fill, outline=outline, width=2)
        if king:
            c.create_oval(x0 + SQUARE / 2 - 8, y0 + SQUARE / 2 - 8,
                          x0 + SQUARE / 2 + 8, y0 + SQUARE / 2 + 8,
                          outline="#d4af37", width=3)

    def _cell_at(self, x, y):
        col = (x - MARGIN) // SQUARE
        row = (y - MARGIN) // SQUARE
        if 0 <= row < 8 and 0 <= col < 8:
            return int(row), int(col)
        return None

    # -- interactions --
    def _on_click(self, event):
        cell = self._cell_at(event.x, event.y)
        if cell is None:
            return
        board, _ = self.model.snapshot()
        r, cc = cell
        if self.selected is None:
            if board[r][cc] != 0:
                self.selected = cell
                self._draw_board()
        else:
            if cell == self.selected:
                self.selected = None
            else:
                self.model.human_move(self.selected, cell)
                self._log("SYS", f"human move {self._name(self.selected)}->{self._name(cell)}")
                self.selected = None
            self._draw_board()

    def _on_right_click(self, event):
        """Right-click a square -> dialog to set any figure on it."""
        cell = self._cell_at(event.x, event.y)
        if cell is None:
            return
        r, c = cell
        rows = [
            [("⛀", 1, "#000"), ("⛁", 2, "#000")],   # white man, white king
            [("⛂", 3, "#000"), ("⛃", 4, "#000")],   # black man, black king
        ]
        choice = piece_picker(self.root, self._name(cell), rows,
                              glyph_font=("Segoe UI Symbol", 26))
        if choice == PICK_CANCEL:
            return
        self.model.set_cell(r, c, choice if choice is not None else 0)
        self._log("SYS", f"set {self._name(cell)} = {choice if choice else 'empty'}")
        self.selected = None
        self._draw_board()

    @staticmethod
    def _name(rc):
        r, c = rc
        return f"{'abcdefgh'[c]}{8 - r}"

    def _send_move(self):
        if self.robot_cli:
            self.robot_cli.send_move_request()
        else:
            self._log("SYS", "Send MOVE ignored — robot not connected to the game")

    def _reset(self):
        self.model.reset()
        self.selected = None
        self._draw_board()
        self._log("SYS", "board reset to the starting position")

    def _toggle_robot(self):
        if self.robot_cli and self.robot_cli.is_alive():
            self.robot_cli.stop(); self.robot_cli = None
            self.rob_btn.config(text="Connect"); return
        try:
            port = int(self.rob_port.get())
        except ValueError:
            self._log("SYS", "invalid robot port"); return
        self.robot_cli = RobotClient(self.model, self.rob_host.get().strip(), port, self.ui_queue)
        self.robot_cli.start()
        self.rob_btn.config(text="Disconnect")

    # -- ui queue --
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
                    self._status(self.rob_lamp, self.rob_status, *payload)
                    if payload[0] in ("off", "error"):
                        self.rob_btn.config(text="Connect")
        except queue.Empty:
            pass
        self.root.after(60, self._pump)

    def _status(self, lamp, label, state, text):
        colors = {"ok": "#4CAF50", "error": "#E05050", "off": "#888"}
        lamp.config(fg=colors.get(state, "#888"))
        label.config(text=text)

    def _log(self, tag, msg):
        self.log.config(state="normal")
        self.log.insert("end", msg + "\n", tag)
        self.log.see("end")
        self.log.config(state="disabled")

    # -- lifecycle --
    def shutdown(self):
        if self.board_srv:
            self.board_srv.stop()
        if self.robot_cli:
            self.robot_cli.stop()

    def _on_close(self):
        self.shutdown()
        self.root.destroy()


def main():
    root = tk.Tk()
    CheckersEmulatorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
