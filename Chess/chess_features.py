# -*- coding: utf-8 -*-
"""
chess_features
==============

GUI feature support for chess_main.py (dev branch):

  * ChessClock        - per-side countdown chess timer with increment
  * GameLog           - rolling on-screen event log (also echoes to console)
  * CapturedTracker   - "trash" pieces captured by each side + material score
  * startup_config()  - tkinter dialog: COM port, difficulty, robot/user move,
                        time control
  * draw_panel()      - renders the whole side panel: connection lamps, clocks,
                        captured pieces, difficulty, mode and the log
  * layout constants  - board/panel geometry used by chess_main

Animation lives in chess_main (it owns the board drawing primitives).
"""

import time
import pygame as p

import chess_ai

# ── Layout ────────────────────────────────────────────────────────────────────
BOARD_X    = 24
BOARD_Y    = 64
BOARD_SIZE = 720
PANEL_X    = BOARD_X + BOARD_SIZE + 20      # 764
PANEL_W    = 360
PANEL_PAD  = 14
BTN_Y      = BOARD_Y + BOARD_SIZE + 8       # button bar under the board
BTN_H      = 40
WINDOW_W   = PANEL_X + PANEL_W + 20         # 1144
WINDOW_H   = BTN_Y + BTN_H + 30             # room for the button bar + hint

# Colours — light / white theme
C_BG       = (245, 246, 248)     # window background
C_PANEL    = (255, 255, 255)     # card background
C_PANEL_HI = (225, 236, 252)     # active highlight
C_BORDER   = (214, 218, 224)     # card border
C_TEXT     = (32, 34, 38)        # primary text (dark)
C_DIM      = (120, 124, 130)     # secondary text
C_ACCENT   = (28, 116, 214)      # blue
C_GREEN    = (40, 158, 64)
C_RED      = (206, 60, 60)
C_GREY     = (188, 190, 194)     # lamp off
C_GOLD     = (176, 132, 16)      # readable gold on white

# Piece values for the material score in the captured tray.
PIECE_VALUE = {"P": 1, "N": 3, "B": 3, "R": 5, "Q": 9, "K": 0}

_FONTS = {}
_ICON_CACHE = {}


def font(size, bold=False):
    key = (size, bold)
    if key not in _FONTS:
        _FONTS[key] = p.font.SysFont("Segoe UI", size, bold=bold)
    return _FONTS[key]


def font_mono(size, bold=False):
    key = ("mono", size, bold)
    if key not in _FONTS:
        _FONTS[key] = p.font.SysFont("Consolas", size, bold=bold)
    return _FONTS[key]


GAP = 8                       # uniform vertical gap between panel cards

PIECE_VALUE_BY_NAME = {"Pawn": 1, "Knight": 3, "Bishop": 3,
                       "Rook": 5, "Queen": 9, "King": 0}


# ── Sound ─────────────────────────────────────────────────────────────────────
_SOUNDS = {
    "move":    [(700, 45)],
    "capture": [(520, 60), (400, 70)],
    "select":  [(880, 35)],
    "error":   [(300, 160), (200, 220)],
    "win":     [(660, 120), (880, 140), (1046, 260)],
    "lose":    [(392, 160), (330, 200), (247, 320)],
    "draw":    [(440, 160), (440, 160)],
}
sound_enabled = True


def play_sound(kind):
    """Play a short non-blocking cue (Windows winsound; silent elsewhere)."""
    if not sound_enabled:
        return
    seq = _SOUNDS.get(kind)
    if not seq:
        return

    def run():
        try:
            import winsound
            for f, d in seq:
                winsound.Beep(f, d)
        except Exception:
            pass
    import threading
    threading.Thread(target=run, daemon=True).start()


# ── Clickable buttons (pygame) ────────────────────────────────────────────────
class Button:
    def __init__(self, key, label, toggle=False):
        self.key = key
        self.label = label
        self.toggle = toggle
        self.on = False
        self.enabled = True
        self.rect = None


def layout_buttons(buttons, x, y, w, h, gap=6):
    n = len(buttons)
    if n == 0:
        return
    bw = (w - gap * (n - 1)) / n
    for i, b in enumerate(buttons):
        b.rect = p.Rect(int(x + i * (bw + gap)), y, int(bw), h)


def draw_buttons(surface, buttons, hover=None):
    for b in buttons:
        if b.rect is None:
            continue
        hovered = hover and b.rect.collidepoint(hover) and b.enabled
        if b.toggle and b.on:
            bg, edge, ew = C_PANEL_HI, C_ACCENT, 2
        elif not b.enabled:
            bg, edge, ew = (228, 229, 232), C_BORDER, 1
        elif hovered:
            bg, edge, ew = (224, 233, 247), C_ACCENT, 1
        else:
            bg, edge, ew = (236, 238, 242), C_BORDER, 1
        p.draw.rect(surface, bg, b.rect, border_radius=6)
        p.draw.rect(surface, edge, b.rect, ew, border_radius=6)
        col = C_TEXT if b.enabled else C_DIM
        label = b.label + ("  ●" if (b.toggle and b.on) else "")
        s = font(12, bold=True).render(label, True, col)
        surface.blit(s, (b.rect.centerx - s.get_width() // 2,
                         b.rect.centery - s.get_height() // 2))


def button_at(buttons, pos):
    for b in buttons:
        if b.rect and b.enabled and b.rect.collidepoint(pos):
            return b
    return None


# ── Chess clock ────────────────────────────────────────────────────────────────
class ChessClock:
    """Two countdown timers. Only the active side ticks down."""

    def __init__(self, base_seconds=300, increment=0):
        self.base = float(base_seconds)
        self.increment = float(increment)
        self.times = {"white": float(base_seconds), "black": float(base_seconds)}
        self.active = None            # 'white' | 'black' | None
        self.running = False
        self.enabled = base_seconds > 0
        self._last = None

    def start(self, side="white"):
        self.active = side
        self.running = True
        self._last = time.monotonic()

    def set_active(self, side):
        """Switch the ticking side (call when the turn changes)."""
        self._sync()
        self.active = side
        self._last = time.monotonic()

    def add_increment(self, side):
        if self.enabled and self.increment:
            self.times[side] += self.increment

    def pause(self):
        self._sync()
        self.running = False

    def resume(self):
        if not self.running:
            self.running = True
            self._last = time.monotonic()

    def tick(self):
        """Call once per frame."""
        if self.enabled:
            self._sync()

    def _sync(self):
        now = time.monotonic()
        if self.running and self.active and self._last is not None:
            self.times[self.active] = max(0.0, self.times[self.active] - (now - self._last))
        self._last = now

    def flagged(self, side):
        return self.enabled and self.times[side] <= 0.0

    def text(self, side):
        if not self.enabled:
            return "--:--"
        t = int(self.times[side] + 0.5)
        return f"{t // 60:02d}:{t % 60:02d}"


# ── Event log ───────────────────────────────────────────────────────────────────
class GameLog:
    TAG_COLORS = {
        "info":  C_TEXT,
        "white": (60, 60, 64),
        "black": (40, 70, 150),
        "ai":    C_ACCENT,
        "warn":  C_GOLD,
        "error": C_RED,
        "ok":    C_GREEN,
        "robot": C_ACCENT,
        "com":   (70, 130, 40),
    }

    def __init__(self, maxlen=300):
        self.maxlen = maxlen
        self.entries = []            # list of (tag, text)

    def add(self, text, tag="info", echo=True):
        self.entries.append((tag, str(text)))
        if len(self.entries) > self.maxlen:
            self.entries = self.entries[-self.maxlen:]
        if echo:
            print(f">>> {text}")

    def recent(self, n):
        return self.entries[-n:]


# ── Move history (representative two-column list) ─────────────────────────────────
class MovesHistory:
    """Numbered move pairs: [move_no, white_text, black_text]."""

    def __init__(self):
        self.rows = []

    def add(self, side, text):
        if side == "white":
            self.rows.append([len(self.rows) + 1, text, ""])
        else:
            if not self.rows:
                self.rows.append([1, "…", text])
            else:
                self.rows[-1][2] = text

    def reset(self):
        self.rows.clear()

    def recent(self, n):
        return self.rows[-n:] if n > 0 else []


def count_material(gs):
    """Return (white_count, black_count, white_value, black_value) from the board."""
    wc = bc = wv = bv = 0
    try:
        for r in range(8):
            for c in range(8):
                pc = gs.board.squares[c, r].get_piece()
                if pc is None:
                    continue
                v = PIECE_VALUE_BY_NAME.get(pc.get_name(), 0)
                if pc.get_color() == "white":
                    wc += 1; wv += v
                else:
                    bc += 1; bv += v
    except Exception:
        pass
    return wc, bc, wv, bv


# ── Captured pieces ("trash") ────────────────────────────────────────────────────
class CapturedTracker:
    def __init__(self):
        # image-name codes (e.g. 'bP') captured BY each colour
        self.by_white = []
        self.by_black = []

    def reset(self):
        self.by_white.clear()
        self.by_black.clear()

    def add_from_move(self, move):
        """Record any piece captured by `move`. Safe against missing fields."""
        if move is None:
            return
        cap = getattr(move, "piece_captured", None)
        mover = getattr(move, "piece_moved", None)
        code = _piece_code(cap)
        if code is None and _contains_enpassant(move):
            # en-passant: the captured pawn is the opposite colour of the mover
            mover_color = _color_of(mover)
            if mover_color:
                code = ("b" if mover_color == "white" else "w") + "P"
        if code is None:
            return
        mover_color = _color_of(mover)
        if mover_color == "white":
            self.by_white.append(code)
        elif mover_color == "black":
            self.by_black.append(code)

    def material_score(self):
        """Positive = white is ahead (in piece value)."""
        w = sum(PIECE_VALUE.get(c[1], 0) for c in self.by_white)
        b = sum(PIECE_VALUE.get(c[1], 0) for c in self.by_black)
        return w - b


def _color_of(piece):
    if piece is None:
        return None
    try:
        return piece.get_color()
    except Exception:
        return None


def _piece_code(piece):
    if piece is None:
        return None
    try:
        if hasattr(piece, "get_image_name"):
            return piece.get_image_name()
        return piece.get_color()[0] + piece.get_name()[0]
    except Exception:
        return None


def _contains_enpassant(move):
    try:
        return bool(move.contains_enpassant())
    except Exception:
        return False


# ── Small icon scaling for the captured tray ─────────────────────────────────────
def _icon(images, code, size):
    key = (code, size)
    if key not in _ICON_CACHE and code in images:
        _ICON_CACHE[key] = p.transform.smoothscale(images[code], (size, size))
    return _ICON_CACHE.get(key)


# ── Panel rendering ──────────────────────────────────────────────────────────────
def _lamp(surface, x, y, on, radius=7):
    color = C_GREEN if on else C_GREY
    p.draw.circle(surface, color, (x, y), radius)
    p.draw.circle(surface, (20, 20, 20), (x, y), radius, 1)


def _section(surface, x, y, w, h, title):
    rect = p.Rect(x, y, w, h)
    p.draw.rect(surface, C_PANEL, rect, border_radius=8)
    p.draw.rect(surface, C_BORDER, rect, 1, border_radius=8)
    if title:
        surface.blit(font(13, bold=True).render(title, True, C_DIM), (x + 10, y + 6))
    return rect


def draw_top_bar(surface, gs, ctx):
    """Caption + whose turn it is, above the board."""
    title = font(20, bold=True).render("Chess — Arduino / UR3", True, C_TEXT)
    surface.blit(title, (BOARD_X, 18))
    turn = "White to move" if getattr(gs, "white_to_move", True) else "Black (AI) thinking…"
    tcol = C_TEXT if getattr(gs, "white_to_move", True) else C_ACCENT
    if ctx.get("state_label"):
        turn = ctx["state_label"]
    label = font(15).render(turn, True, tcol)
    surface.blit(label, (BOARD_X + 320, 24))


def draw_panel(surface, gs, ctx):
    """
    Render the whole side panel.

    ctx keys:
      clock, log, captured, images,
      arduino_connected, robot_connected, robot_mode (bool send-to-robot),
      difficulty (str), port (str)
    """
    x, w = PANEL_X, PANEL_W
    frame = p.Rect(x - 6, BOARD_Y - 6, w + 12, BOARD_SIZE + 12)
    p.draw.rect(surface, C_BG, frame, border_radius=10)
    p.draw.rect(surface, C_BORDER, frame, 1, border_radius=10)
    y = BOARD_Y

    # --- Connections ---
    h = 62
    _section(surface, x, y, w, h, "CONNECTIONS")
    cy = y + 30
    _lamp(surface, x + 18, cy, ctx.get("arduino_connected"))
    surface.blit(font(13).render(f"Arduino  {ctx.get('port','?')}", True, C_TEXT), (x + 34, cy - 8))
    st = "connected" if ctx.get("arduino_connected") else "—"
    surface.blit(font(11).render(st, True, C_GREEN if ctx.get("arduino_connected") else C_DIM),
                 (x + w - 86, cy - 7))
    cy += 22
    _lamp(surface, x + 18, cy, ctx.get("robot_connected"))
    surface.blit(font(13).render("UR3 robot", True, C_TEXT), (x + 34, cy - 8))
    st = "connected" if ctx.get("robot_connected") else "waiting"
    surface.blit(font(11).render(st, True, C_GREEN if ctx.get("robot_connected") else C_DIM),
                 (x + w - 86, cy - 7))
    y += h + GAP

    # --- Score ---
    h = 50
    _section(surface, x, y, w, h, "SCORE")
    wc, bc, wv, bv = count_material(gs)
    surface.blit(font(15, bold=True).render(f"White  {wc}", True, (40, 60, 120)), (x + 12, y + 24))
    surface.blit(font(15, bold=True).render(f"Black  {bc}", True, (130, 45, 45)),
                 (x + w // 2 + 8, y + 24))
    adv = wv - bv
    if adv:
        txt = f"{'White' if adv > 0 else 'Black'} +{abs(adv)}"
        surface.blit(font(12, bold=True).render(txt, True, C_GOLD), (x + w - 84, y + 6))
    y += h + GAP

    # --- Clocks ---
    clock = ctx.get("clock")
    h = 78
    _section(surface, x, y, w, h, "CLOCK")
    if clock:
        bw = (w - 30) // 2
        _clock_box(surface, x + 10, y + 24, bw, "WHITE", clock, "white", gs)
        _clock_box(surface, x + 20 + bw, y + 24, bw, "BLACK", clock, "black", gs)
    y += h + GAP

    # --- Difficulty + mode ---
    h = 58
    _section(surface, x, y, w, h, "AI / MODE")
    surface.blit(font(13).render(f"Difficulty:  {ctx.get('difficulty','?')}", True, C_TEXT),
                 (x + 12, y + 24))
    mode = "Robot moves black" if ctx.get("robot_mode") else "User moves black"
    surface.blit(font(13).render(f"Mode:  {mode}", True, C_TEXT), (x + 12, y + 40))
    y += h + GAP

    # --- Captured / trash ---
    cap = ctx.get("captured")
    h = 86
    _section(surface, x, y, w, h, "CAPTURED (TRASH)")
    if cap:
        _captured_row(surface, x + 10, y + 26, "White took:", cap.by_white, ctx["images"], icon=22)
        _captured_row(surface, x + 10, y + 54, "Black took:", cap.by_black, ctx["images"], icon=22)
    y += h + GAP

    # --- Move history ---
    moves = ctx.get("moves")
    h = 140
    _section(surface, x, y, w, h, "MOVE HISTORY")
    if moves:
        line_h = 16
        max_rows = (h - 28) // line_h
        for i, (num, ws, bs) in enumerate(moves.recent(max_rows)):
            line = f"{num:>2}. {ws:<7} {bs}"
            surface.blit(font_mono(12).render(line, True, C_TEXT), (x + 12, y + 26 + i * line_h))
    y += h + GAP

    # --- Log (fills remaining height) ---
    h = BOARD_Y + BOARD_SIZE - y
    _section(surface, x, y, w, h, "LOG")
    log = ctx.get("log")
    if log:
        line_h = 16
        max_lines = max(0, (h - 28) // line_h)
        for i, (tag, text) in enumerate(log.recent(max_lines)):
            color = GameLog.TAG_COLORS.get(tag, C_TEXT)
            s = text if len(text) <= 46 else text[:45] + "…"
            surface.blit(font(12).render(s, True, color), (x + 10, y + 24 + i * line_h))

    # (button bar + hint are drawn by chess_main below the board)


def _clock_box(surface, x, y, w, label, clock, side, gs):
    active = clock.running and clock.active == side
    bg = C_PANEL_HI if active else C_PANEL
    rect = p.Rect(x, y, w, 48)
    p.draw.rect(surface, bg, rect, border_radius=6)
    p.draw.rect(surface, C_ACCENT if active else C_BORDER, rect, 2, border_radius=6)
    surface.blit(font(11, bold=True).render(label, True, C_DIM), (x + 8, y + 4))
    flagged = clock.flagged(side)
    col = C_RED if flagged else (C_TEXT if active else C_DIM)
    surface.blit(font(22, bold=True).render(clock.text(side), True, col), (x + 8, y + 18))


def _captured_row(surface, x, y, label, codes, images, icon=24):
    surface.blit(font(12).render(label, True, C_DIM), (x, y + 4))
    cx = x + 86
    for code in codes:
        ic = _icon(images, code, icon)
        if ic:
            surface.blit(ic, (cx, y))
            cx += icon - 6
        if cx > PANEL_X + PANEL_W - 40:
            break


# ── Startup configuration dialog ─────────────────────────────────────────────────
TC_VALUES = {
    "No clock": (0, 0),
    "1 | 0": (60, 0), "3 | 2": (180, 2), "5 | 0": (300, 0),
    "10 | 0": (600, 0), "10 | 5": (600, 5),
    "15 | 0": (900, 0), "15 | 10": (900, 10),
    "20 | 0": (1200, 0), "25 | 0": (1500, 0), "30 | 0": (1800, 0),
    "45 | 0": (2700, 0), "60 | 0": (3600, 0), "90 | 30": (5400, 30),
}


def startup_config(default_port="COM5"):
    """
    Tkinter setup dialog. Loads saved settings as defaults, and on Start saves
    them back to chess_settings.json. Returns a dict:
        {ok, port, baud, difficulty, robot_mode, base_seconds, increment,
         humanWhite, humanBlack, robot_host, robot_port,
         window_scale, dpi_aware, start_fullscreen}
    `ok` is False if the user closed the dialog.
    """
    import tkinter as tk
    from tkinter import ttk
    import chess_settings

    saved = chess_settings.load()
    try:
        import serial.tools.list_ports as list_ports
        ports = [p_.device for p_ in list_ports.comports()]
    except Exception:
        ports = []
    start_port = saved.get("serial_port") or default_port
    if start_port not in ports:
        ports.insert(0, start_port)
    if not ports:
        ports = [start_port]

    result = {"ok": False}

    BG, FG, CARD, ACC = "#f4f5f7", "#222428", "#ffffff", "#1c74d6"
    root = tk.Tk()
    root.title("Chess — Setup")
    root.configure(bg=BG)
    root.resizable(False, False)

    tk.Label(root, text="Chess  —  game setup", bg=BG, fg="#111",
             font=("Segoe UI", 15, "bold")).pack(padx=16, pady=(14, 2), anchor="w")
    tk.Label(root, text="Configure the game, hardware and display before you start",
             bg=BG, fg="#6c7078", font=("Segoe UI", 9)).pack(padx=16, anchor="w")

    body = tk.Frame(root, bg=BG)
    body.pack(fill="both", expand=True, padx=8, pady=8)
    col_l = tk.Frame(body, bg=BG); col_l.pack(side="left", fill="both", expand=True)
    col_r = tk.Frame(body, bg=BG); col_r.pack(side="left", fill="both", expand=True)

    def section(parent, title):
        lf = tk.LabelFrame(parent, text=title, bg=CARD, fg=ACC,
                           font=("Segoe UI", 10, "bold"), padx=10, pady=8,
                           bd=1, relief="solid")
        lf.pack(fill="x", padx=8, pady=(0, 10))
        return lf

    def row(parent, text):
        r = tk.Frame(parent, bg=CARD); r.pack(fill="x", pady=3)
        tk.Label(r, text=text, bg=CARD, fg=FG, font=("Segoe UI", 10),
                 width=17, anchor="w").pack(side="left")
        return r

    # ── Game ──
    game = section(col_l, "Game")
    r = row(game, "Difficulty")
    diff_var = tk.StringVar(value=saved.get("difficulty", "Easy"))
    ttk.Combobox(r, textvariable=diff_var, values=list(chess_ai.DIFFICULTY_PRESETS),
                 width=14, state="readonly").pack(side="left")

    r = row(game, "Black moved by")
    mode_var = tk.StringVar(value="robot" if saved.get("robot_mode", True) else "user")
    tk.Radiobutton(r, text="UR3 robot", variable=mode_var, value="robot",
                   bg=CARD, fg=FG, selectcolor="#dde6f5").pack(side="left")
    tk.Radiobutton(r, text="User", variable=mode_var, value="user",
                   bg=CARD, fg=FG, selectcolor="#dde6f5").pack(side="left")

    r = row(game, "Time control")
    tc_var = tk.StringVar(value=saved.get("time_control", "15 | 0"))
    ttk.Combobox(r, textvariable=tc_var, values=list(TC_VALUES), width=14,
                 state="readonly").pack(side="left")

    # ── Display / scaling ──
    disp = section(col_l, "Display  /  Scaling")
    r = row(disp, "Window scale")
    scale_var = tk.DoubleVar(value=float(saved.get("window_scale", 1.0)))
    tk.Spinbox(r, from_=0.6, to=2.5, increment=0.1, width=7,
               textvariable=scale_var, format="%.1f").pack(side="left")
    tk.Label(r, text="x  window size", bg=CARD, fg="#6c7078",
             font=("Segoe UI", 9)).pack(side="left", padx=6)

    dpi_var = tk.BooleanVar(value=bool(saved.get("dpi_aware", True)))
    tk.Checkbutton(disp, text="High-DPI aware (recommended for 4K screens)",
                   variable=dpi_var, bg=CARD, fg=FG, selectcolor=CARD,
                   font=("Segoe UI", 10)).pack(anchor="w", pady=(6, 0))
    fs_var = tk.BooleanVar(value=bool(saved.get("start_fullscreen", False)))
    tk.Checkbutton(disp, text="Start in full-screen (F11 toggles later)",
                   variable=fs_var, bg=CARD, fg=FG, selectcolor=CARD,
                   font=("Segoe UI", 10)).pack(anchor="w")

    # ── Sensor board ──
    board = section(col_r, "Sensor Board (Arduino)")
    r = row(board, "COM port")
    port_var = tk.StringVar(value=start_port)
    ttk.Combobox(r, textvariable=port_var, values=ports, width=14).pack(side="left")
    r = row(board, "Baud")
    baud_var = tk.IntVar(value=int(saved.get("baud", 9600)))
    ttk.Combobox(r, textvariable=baud_var, width=12,
                 values=[9600, 19200, 38400, 57600, 115200]).pack(side="left")

    # ── UR3 robot ──
    robot = section(col_r, "UR3 Robot")
    r = row(robot, "Listen host")
    rhost_var = tk.StringVar(value=saved.get("robot_host", "0.0.0.0"))
    tk.Entry(r, textvariable=rhost_var, width=16).pack(side="left")
    r = row(robot, "Listen port")
    rport_var = tk.IntVar(value=int(saved.get("robot_port", 3000)))
    tk.Entry(r, textvariable=rport_var, width=8).pack(side="left")
    tk.Label(robot, text="The robot connects to this PC on the port above.",
             bg=CARD, fg="#6c7078", font=("Segoe UI", 9),
             wraplength=250, justify="left").pack(anchor="w", pady=(4, 0))

    def start():
        try:
            base, inc = TC_VALUES.get(tc_var.get(), (300, 0))
            cfg = dict(
                ok=True,
                port=port_var.get().strip() or start_port,
                baud=int(baud_var.get()),
                difficulty=diff_var.get(),
                robot_mode=(mode_var.get() == "robot"),
                base_seconds=base,
                increment=inc,
                time_control=tc_var.get(),
                humanWhite=True,
                humanBlack=False,
                robot_host=rhost_var.get().strip() or "0.0.0.0",
                robot_port=int(rport_var.get()),
                window_scale=round(float(scale_var.get()), 2),
                dpi_aware=bool(dpi_var.get()),
                start_fullscreen=bool(fs_var.get()),
            )
        except (ValueError, tk.TclError) as e:
            from tkinter import messagebox
            messagebox.showerror("Invalid setting",
                                 f"Please check the numeric fields.\n\n{e}")
            return
        # Persist the choices for next time.
        chess_settings.save({
            "serial_port": cfg["port"], "baud": cfg["baud"],
            "robot_host": cfg["robot_host"], "robot_port": cfg["robot_port"],
            "difficulty": cfg["difficulty"], "robot_mode": cfg["robot_mode"],
            "time_control": cfg["time_control"], "window_scale": cfg["window_scale"],
            "dpi_aware": cfg["dpi_aware"], "start_fullscreen": cfg["start_fullscreen"],
        })
        result.update(cfg)
        root.destroy()

    bar = tk.Frame(root, bg=BG)
    bar.pack(fill="x", padx=16, pady=(0, 16))
    tk.Button(bar, text="Start game", font=("Segoe UI", 11, "bold"),
              bg="#4C9A2A", fg="white", width=16, command=start).pack(side="right")

    root.update_idletasks()
    w, h = root.winfo_width(), root.winfo_height()
    x = (root.winfo_screenwidth() - w) // 2
    y = (root.winfo_screenheight() - h) // 3
    root.geometry(f"+{x}+{y}")
    root.bind("<Return>", lambda e: start())
    root.mainloop()
    return result
