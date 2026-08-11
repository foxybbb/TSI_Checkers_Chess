#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import time
import serial
import json
import pygame as p
import tkinter as tk
from tkinter import messagebox
import socket
import threading
import chess_engine
import chess_ai as ai
from chess_move import Move          # lightweight stub used by find_move_from_snapshots
from chess_themes import themes
from chess_menu import mainMenu
import chess_features as feat

CAPTION = 'Chess - Arduino Sync'
WIDTH = HEIGHT = 720
DIMENSION = 8
SQ_SIZE = HEIGHT // DIMENSION
MAX_FPS = 120
IMAGES = {}
FLIPPEDBOARD = [i for i in reversed(range(DIMENSION))]
UPSIDEDOWN = False
BACKGROUND_COLOR = p.Color(245, 246, 248)  # Light theme background

# Fullscreen / scaling: everything is drawn onto a fixed-size logical `frame`,
# then presented (scaled + centred) onto the real display so it works the same
# windowed, resized, or fullscreen.
frame = None
fullscreen = False
_present = (1.0, 0, 0)        # (scale, offset_x, offset_y) — for mouse mapping


def present():
    """Scale the logical frame to the display, preserving aspect, centred."""
    global _present
    sw, sh = screen.get_size()
    fw, fh = frame.get_size()
    scale = min(sw / fw, sh / fh)
    tw, th = max(1, int(fw * scale)), max(1, int(fh * scale))
    ox, oy = (sw - tw) // 2, (sh - th) // 2
    _present = (scale, ox, oy)
    screen.fill((18, 18, 20))
    screen.blit(p.transform.smoothscale(frame, (tw, th)), (ox, oy))
    p.display.flip()


def frame_pos(display_pos):
    """Map a display (mouse) position back to logical frame coordinates."""
    scale, ox, oy = _present
    return ((display_pos[0] - ox) / scale, (display_pos[1] - oy) / scale)


def toggle_fullscreen():
    global screen, fullscreen
    fullscreen = not fullscreen
    if fullscreen:
        screen = p.display.set_mode((0, 0), p.FULLSCREEN)
    else:
        screen = p.display.set_mode((feat.WINDOW_W, feat.WINDOW_H), p.RESIZABLE)


def apply_dpi_awareness(enable=True):
    """Ask Windows for DPI awareness so the window is crisp on 4K screens.
    Must be called before the display is created."""
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

# --- НАСТРОЙКИ ARDUINO ---
SERIAL_PORT = 'COM21'
BAUD_RATE = 9600

# --- GAME STATES ---
WAITING_FOR_HUMAN = 0
PROCESSING_AI = 1

class ArduinoHandler:
    def __init__(self, port=None):
        self.ser = None
        self.port = port or SERIAL_PORT
        try:
            self.ser = serial.Serial(self.port, BAUD_RATE, timeout=2)
            print(f"[Arduino] Подключено к {self.port}")
            time.sleep(2)
        except Exception as e:
            print(f"[Arduino] Ошибка: {e}")

    @property
    def connected(self):
        return bool(self.ser and getattr(self.ser, "is_open", False))

    def close(self):
        if self.ser and self.ser.is_open:
            self.ser.close()

    def get_board_state(self):
        if not self.ser or not self.ser.is_open:
            return None
        try:
            self.ser.reset_input_buffer()
            self.ser.reset_output_buffer()
            time.sleep(0.1)
            
            # Отправляем команду
            self.ser.write(b'a\n')
            self.ser.flush()

            time.sleep(5)

            # Читаем строку до \n (Ардуино шлет \r\n в конце строки)
            # Если прилетело эхо "a" или пустая строка, читаем следующую
            raw_bytes = self.ser.readline()
            if len(raw_bytes) < 10:
                raw_bytes = self.ser.readline()
            
            # Декодируем и убираем лишние пробелы/переносы
            text = raw_bytes.decode('ascii', errors='ignore').strip()
            
            if not text:
                print("[Arduino] Ошибка: получены пустые данные или таймаут.")
                return None
                
            # Парсим строку по пробелам
            try:
                int_list = [int(x) for x in text.split() if x]
            except ValueError:
                print("[Arduino] Ошибка: невозможно распарсить текст с пробелами.")
                return None
                
            if len(int_list) >= 64:
                int_list = int_list[:64]
                parsed = self._parse_raw_data(int_list)
                pieces_found = sum(1 for p in parsed if p is not None)
                print(f"[Arduino] Распознано фигур: {pieces_found}")
                return parsed
            else:
                print(f"[Arduino] Ошибка: получено {len(int_list)} значений, ожидалось 64.")
                return None

        except Exception as e:
            print(f"[Arduino] КРИТИЧЕСКАЯ ОШИБКА при чтении/парсинге данных: {e}")
            return None

    def _parse_raw_data(self, int_list):
        parsed_board = []
        # Маппинг: ID -> Название класса
        ID_TO_TYPE = {2: 'Pawn', 3: 'Rook', 4: 'Knight', 5: 'Bishop', 6: 'Queen', 7: 'King'}
        
        for val in int_list:
            if val == 0:
                parsed_board.append(None)
            elif val == 1:  # Явное исключение для черной пешки (Ардуино присылает 1 вместо 4)
                parsed_board.append({'color': 'black', 'type': 'Pawn'})
            else:
                color_bit = val & 0x01
                type_id = val >> 1
                color = 'white' if color_bit == 1 else 'black'
                p_type = ID_TO_TYPE.get(type_id, None)
                if p_type:
                    parsed_board.append({'color': color, 'type': p_type})
                else:
                    parsed_board.append(None)
        return parsed_board
 
def sync_entire_board(gs, arduino_data):
    """
    Synchronise the engine board from Arduino sensor data.
    Delegates entirely to GameState.sync_from_arduino() which builds a FEN
    and loads it into the python-chess board.
    """
    gs.sync_from_arduino(arduino_data)

def get_board_snapshot(gs):
    """Создает 'снимок' доски для последующего сравнения."""
    snapshot = {}
    for r in range(DIMENSION):
        for c in range(DIMENSION):
            # Правильный порядок индексов: squares[col, row]
            piece = gs.board.squares[c, r].get_piece()
            if piece:
                # Сохраняем как (col, row) для согласованности
                snapshot[(c, r)] = f"{piece.get_color()}{piece.get_name()}"
    return snapshot

def find_move_from_snapshots(old_snapshot, new_snapshot, gs):
    """Находит ход, сравнивая два состояния доски. Улучшенная версия с проверками."""
    old_keys = set(old_snapshot.keys())
    new_keys = set(new_snapshot.keys())
    
    emptied = old_keys - new_keys
    filled = new_keys - old_keys
    changed = {k for k in old_keys & new_keys if old_snapshot[k] != new_snapshot[k]}
    
    moved_from = None
    moved_to = None
    
    turn_color = 'white' if gs.white_to_move else 'black'
    
    # Case A: Normal move or promotion without capture
    if len(emptied) == 1 and len(filled) == 1 and len(changed) == 0:
        moved_from = list(emptied)[0]
        moved_to = list(filled)[0]
        
    # Case B: Capture or promotion with capture
    elif len(emptied) == 1 and len(filled) == 0 and len(changed) == 1:
        moved_from = list(emptied)[0]
        moved_to = list(changed)[0]
        
    # Case C: Castling (2 emptied, 2 filled)
    elif len(emptied) == 2 and len(filled) == 2 and len(changed) == 0:
        king_move_from = None
        king_move_to = None
        for sq in emptied:
            if "King" in old_snapshot.get(sq, ""):
                king_move_from = sq
                break
        for sq in filled:
            if "King" in new_snapshot.get(sq, ""):
                king_move_to = sq
                break
        if king_move_from and king_move_to:
            moved_from = king_move_from
            moved_to = king_move_to
            
    # Case D: En Passant (2 emptied, 1 filled)
    elif len(emptied) == 2 and len(filled) == 1 and len(changed) == 0:
        start_cand = None
        for sq in emptied:
            sig = old_snapshot.get(sq, "")
            if sig.startswith(turn_color):
                start_cand = sq
                break
        if start_cand:
            moved_from = start_cand
            moved_to = list(filled)[0]

    # If we found start and end squares, validate and return a candidate Move
    if moved_from and moved_to:
        col_from, row_from = moved_from
        col_to, row_to = moved_to
        
        if not (0 <= col_from < DIMENSION and 0 <= row_from < DIMENSION):
            print(f"[DEBUG] Неверные координаты: from ({col_from}, {row_from})")
            return None
        if not (0 <= col_to < DIMENSION and 0 <= row_to < DIMENSION):
            print(f"[DEBUG] Неверные координаты: to ({col_to}, {row_to})")
            return None
            
        start_sq = gs.board.squares[col_from, row_from]
        end_sq = gs.board.squares[col_to, row_to]
        
        # КРИТИЧЕСКАЯ ПРОВЕРКА: есть ли фигура на начальной клетке?
        if start_sq.get_piece() is None:
            print(f"[DEBUG] ОШИБКА: На клетке {start_sq.get_name()} нет фигуры!")
            print(f"[DEBUG] Возможно несоответствие между физической доской и игровым состоянием.")
            return None
            
        return Move(start_sq, end_sq, gs.move_number)
        
    print(f"[DEBUG] Не удалось определить ход.")
    print(f"[DEBUG] Изменения: {len(emptied)} удалено, {len(filled)} добавлено, {len(changed)} изменено")
    if emptied:
        print(f"[DEBUG] Удаленные позиции: {emptied} (значения: {[old_snapshot[k] for k in emptied]})")
    if filled:
        print(f"[DEBUG] Добавленные позиции: {filled} (значения: {[new_snapshot[k] for k in filled]})")
    if changed:
        print(f"[DEBUG] Измененные позиции: {changed} (было: {[old_snapshot[k] for k in changed]}, стало: {[new_snapshot[k] for k in changed]})")
    return None

def get_board_snapshot_from_data(data):
    """Создает 'снимок' доски из данных, полученных от Arduino."""
    snapshot = {}
    for r in range(DIMENSION):
        for c in range(DIMENSION):
            # Arduino data: index = row * 8 + col (row-major order)
            piece = data[r * DIMENSION + c]
            if piece:
                # Сохраняем как (col, row) для согласованности с get_board_snapshot
                snapshot[(c, r)] = f"{piece['color']}{piece['type']}"
    return snapshot

def handle_game_over(title, message):
    """
    Shows a simple window asking the user if they want to play again.
    Returns True if Yes, False if No.
    """
    root = tk.Tk()
    root.withdraw()
    root.attributes('-topmost', True)
    prompt = message + "\n\nСначала РАССТАВЬТЕ фигурки на доске в начальное положение, " \
                       "затем нажмите 'Да', чтобы сыграть еще раз."
    result = messagebox.askyesno(title, prompt)
    root.destroy()
    return result


def show_error(title, problem, advice):
    """Error dialog stating WHAT is wrong and WHAT TO DO about it."""
    if isinstance(advice, (list, tuple)):
        advice = "\n".join(f"  •  {a}" for a in advice)
    try:
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        messagebox.showerror(title, f"{problem}\n\nWhat to do:\n{advice}")
        root.destroy()
    except Exception as e:
        print(f"[ERROR DIALOG] {title}: {problem} | {e}")


def show_unrecognised(problem, advice):
    """
    Dialog for a move that couldn't be read from the board. Offers two recovery
    actions. Returns:
        "resync" -> re-read the WHOLE board from the Arduino (rebuild state)
        "retry"  -> scan/update the board again (normal retry)
        None     -> cancel
    """
    result = {"choice": None}
    try:
        root = tk.Tk()
        root.withdraw()
        win = tk.Toplevel(root)
        win.title("Move not recognised")
        win.configure(bg="#f4f5f7")
        win.attributes("-topmost", True)
        win.resizable(False, False)

        tk.Frame(win, bg="#d23b3b", height=5, width=380).pack(fill="x")
        tk.Label(win, text="Move not recognised", bg="#f4f5f7", fg="#b00020",
                 font=("Segoe UI", 14, "bold")).pack(anchor="w", padx=18, pady=(14, 2))
        tk.Label(win, text=problem, bg="#f4f5f7", fg="#222", justify="left",
                 wraplength=340, font=("Segoe UI", 10)).pack(anchor="w", padx=18, pady=(0, 8))
        for a in advice:
            tk.Label(win, text=f"•  {a}", bg="#f4f5f7", fg="#333", justify="left",
                     wraplength=340, font=("Segoe UI", 10)).pack(anchor="w", padx=18)

        bar = tk.Frame(win, bg="#f4f5f7")
        bar.pack(fill="x", padx=18, pady=16)

        def pick(choice):
            result["choice"] = choice
            win.destroy()

        # RED: full resync of the whole board
        tk.Button(bar, text="Resync ALL board", bg="#d23b3b", fg="white",
                  activebackground="#b22", activeforeground="white",
                  font=("Segoe UI", 10, "bold"), width=18,
                  command=lambda: pick("resync")).pack(side="left")
        # Normal: scan/update the board again
        tk.Button(bar, text="Update board again", bg="#4C9A2A", fg="white",
                  font=("Segoe UI", 10, "bold"), width=18,
                  command=lambda: pick("retry")).pack(side="left", padx=8)
        tk.Button(bar, text="Cancel", font=("Segoe UI", 10), width=8,
                  command=lambda: pick(None)).pack(side="right")

        win.protocol("WM_DELETE_WINDOW", lambda: pick(None))
        win.grab_set()
        win.wait_window()
        root.destroy()
    except Exception as e:
        print(f"[UNRECOGNISED DIALOG] {problem} | {e}")
    return result["choice"]


# Order pieces appear in the "moves you can make" list.
_PIECE_ORDER = {"Pawn": 5, "Knight": 1, "Bishop": 2, "Rook": 3, "Queen": 4, "King": 6}
_PIECE_GLYPH = {"Pawn": "♙", "Knight": "♘", "Bishop": "♗",
                "Rook": "♖", "Queen": "♕", "King": "♔"}


def _legal_moves_by_piece(valid_moves):
    """Group legal moves -> {(name, from_sq): [to_sq, ...]} as readable strings."""
    groups = {}
    for m in valid_moves:
        try:
            name = m.piece_moved.get_name()
            frm = m.start_square.get_name()
            to = m.end_square.get_name()
        except Exception:
            continue
        groups.setdefault((name, frm), []).append(to)
    rows = []
    for (name, frm), tos in groups.items():
        rows.append((name, frm, sorted(set(tos))))
    rows.sort(key=lambda r: (_PIECE_ORDER.get(r[0], 9), r[1]))
    return rows


class _MiniBoardAnim:
    """A small cropped board that animates the WRONG move (red ✗) then a RIGHT
    move (green ✓), looping — embedded in the illegal-move dialog."""
    FILES = "abcdefgh"
    RANKS = "87654321"

    def __init__(self, parent, gs, wrong, right, cell=40):
        self.gs = gs
        self.cell = cell
        self.wrong = wrong          # (from_name, to_name) or None
        self.right = right
        self.imgs = {}
        self._after = None

        sqs = []
        for mv in (wrong, right):
            if mv:
                sqs += [self._cr(mv[0]), self._cr(mv[1])]
        if not sqs:
            sqs = [(0, 0), (7, 7)]
        cs = [c for c, r in sqs]; rs = [r for c, r in sqs]
        self.c0, self.c1 = max(0, min(cs) - 1), min(7, max(cs) + 1)
        self.r0, self.r1 = max(0, min(rs) - 1), min(7, max(rs) + 1)
        w = (self.c1 - self.c0 + 1) * cell
        h = (self.r1 - self.r0 + 1) * cell
        self.canvas = tk.Canvas(parent, width=w, height=h, highlightthickness=1,
                                highlightbackground="#cccccc", bg="#ffffff")
        self._load_images()
        self.phase = 0 if wrong else 1
        self.t = 0.0
        self._tick()

    def widget(self):
        return self.canvas

    def _cr(self, name):
        return self.FILES.index(name[0]), self.RANKS.index(name[1])

    def _load_images(self):
        script = os.path.dirname(os.path.abspath(__file__))
        for code in ("wP wR wN wB wQ wK bP bR bN bB bQ bK").split():
            try:
                img = tk.PhotoImage(file=os.path.join(script, "images", code + ".png"))
                factor = max(1, round(img.width() / self.cell))
                if factor > 1:
                    img = img.subsample(factor, factor)
                self.imgs[code] = img
            except Exception:
                pass

    def _piece_code(self, c, r):
        try:
            pc = self.gs.board.squares[c, r].get_piece()
            if pc is None:
                return None
            return pc.get_image_name() if hasattr(pc, "get_image_name") else \
                pc.get_color()[0] + pc.get_name()[0]
        except Exception:
            return None

    def _draw_piece(self, x, y, code, skip_origin=None, cr=None):
        if code is None or cr == skip_origin:
            return
        cell = self.cell
        img = self.imgs.get(code)
        if img is not None:
            self.canvas.create_image(x + 2, y + 2, anchor="nw", image=img)
        else:
            white = code[0] == "w"
            self.canvas.create_oval(x + 6, y + 6, x + cell - 6, y + cell - 6,
                                    fill="#f1e3c6" if white else "#2a2018",
                                    outline="#555")
            self.canvas.create_text(x + cell / 2, y + cell / 2, text=code[1],
                                    fill="#000" if white else "#fff",
                                    font=("Segoe UI", 12, "bold"))

    def _tick(self):
        cell = self.cell
        c = self.canvas
        c.delete("all")
        mv = self.wrong if self.phase == 0 else self.right
        color = "#d23b3b" if self.phase == 0 else "#2e9e40"
        mfrom = self._cr(mv[0]) if mv else None
        # squares + static pieces
        for cc in range(self.c0, self.c1 + 1):
            for rr in range(self.r0, self.r1 + 1):
                x = (cc - self.c0) * cell
                y = (rr - self.r0) * cell
                light = (cc + rr) % 2 == 0
                c.create_rectangle(x, y, x + cell, y + cell,
                                   fill="#EEEED2" if light else "#769656", width=0)
                self._draw_piece(x, y, self._piece_code(cc, rr),
                                 skip_origin=mfrom, cr=(cc, rr))
        if mv:
            fc, fr = self._cr(mv[0]); tc, tr = self._cr(mv[1])
            t = min(1.0, self.t)
            cx = fc + (tc - fc) * t; cy = fr + (tr - fr) * t
            # arrow
            x1 = (fc - self.c0) * cell + cell / 2; y1 = (fr - self.r0) * cell + cell / 2
            x2 = (tc - self.c0) * cell + cell / 2; y2 = (tr - self.r0) * cell + cell / 2
            c.create_line(x1, y1, x2, y2, fill=color, width=3, arrow="last")
            # sliding piece
            px = (cx - self.c0) * cell; py = (cy - self.r0) * cell
            self._draw_piece(px, py, self._piece_code(fc, fr))
            # verdict mark at destination once arrived
            if t >= 1.0:
                mark = "✗" if self.phase == 0 else "✓"
                c.create_text(x2, y2 - cell * 0.1, text=mark, fill=color,
                              font=("Segoe UI", int(cell * 0.6), "bold"))
        # advance
        self.t += 0.06
        if self.t >= 1.6:
            self.t = 0.0
            # toggle to the other available phase
            other = 1 - self.phase
            other_mv = self.wrong if other == 0 else self.right
            self.phase = other if other_mv else self.phase
        self._after = self.canvas.after(45, self._tick)

    def stop(self):
        if self._after:
            try:
                self.canvas.after_cancel(self._after)
            except Exception:
                pass


def show_move_help(problem, advice, valid_moves, attempted=None, gs=None):
    """
    Custom popup:
      LEFT  = what is wrong + what to do + an animated cropped board that shows
              the WRONG move (red ✗) then a RIGHT move (green ✓).
      RIGHT = the legal moves you CAN make now, grouped by piece.
    `attempted` = (from_sq, to_sq) the player tried.
    """
    src = attempted[0] if attempted else None
    wrong = attempted if (attempted and gs) else None
    # pick a representative legal move (prefer one from the attempted square)
    right = None
    try:
        cand = [m for m in valid_moves if m.start_square.get_name() == src] or list(valid_moves)
        if cand:
            right = (cand[0].start_square.get_name(), cand[0].end_square.get_name())
    except Exception:
        right = None

    anim = None
    try:
        root = tk.Tk()
        root.withdraw()
        win = tk.Toplevel(root)
        win.title("Illegal move")
        win.configure(bg="#f4f5f7")
        win.attributes("-topmost", True)
        win.resizable(False, False)

        # ---- LEFT: what is wrong + animated board ----
        left = tk.Frame(win, bg="#f4f5f7")
        left.grid(row=0, column=0, sticky="nw", padx=(16, 10), pady=16)
        tk.Label(left, text="Move not allowed", bg="#f4f5f7", fg="#b00020",
                 font=("Segoe UI", 13, "bold")).pack(anchor="w")
        tk.Label(left, text=problem, bg="#f4f5f7", fg="#222", justify="left",
                 wraplength=280, font=("Segoe UI", 10)).pack(anchor="w", pady=(6, 8))
        tk.Label(left, text="What to do:", bg="#f4f5f7", fg="#333",
                 font=("Segoe UI", 10, "bold")).pack(anchor="w")
        for a in advice:
            tk.Label(left, text=f"•  {a}", bg="#f4f5f7", fg="#333", justify="left",
                     wraplength=280, font=("Segoe UI", 10)).pack(anchor="w")
        if gs is not None and (wrong or right):
            tk.Label(left, text="red ✗ = your move    green ✓ = a legal move",
                     bg="#f4f5f7", fg="#777", font=("Segoe UI", 8)).pack(anchor="w", pady=(8, 2))
            try:
                anim = _MiniBoardAnim(left, gs, wrong, right)
                anim.widget().pack(anchor="w", pady=(2, 0))
            except Exception as e:
                print(f"[mini board] {e}")

        # ---- RIGHT: legal moves you CAN make ----
        right_f = tk.Frame(win, bg="#ffffff", highlightbackground="#d6dae0",
                           highlightthickness=1)
        right_f.grid(row=0, column=1, sticky="nsew", padx=(0, 16), pady=16)
        tk.Label(right_f, text="Moves you can make now", bg="#ffffff", fg="#1c74d6",
                 font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=10, pady=(8, 4))
        txt = tk.Text(right_f, width=32, height=16, bg="#ffffff", relief="flat",
                      font=("Consolas", 10), wrap="none")
        txt.pack(fill="both", expand=True, padx=10, pady=(0, 8))
        rows = _legal_moves_by_piece(valid_moves)
        if not rows:
            txt.insert("end", "No legal moves — the game may be over.\n")
        for name, frm, tos in rows:
            glyph = _PIECE_GLYPH.get(name, "")
            tag = "hot" if src and frm == src else "norm"
            txt.insert("end", f"{glyph} {name} {frm} → {', '.join(tos)}\n", tag)
        txt.tag_config("hot", foreground="#b00020", font=("Consolas", 10, "bold"))
        txt.tag_config("norm", foreground="#222")
        txt.config(state="disabled")

        def close():
            if anim:
                anim.stop()
            win.destroy()
        tk.Button(win, text="OK", width=12, command=close,
                  font=("Segoe UI", 10, "bold")).grid(row=1, column=0, columnspan=2,
                                                      pady=(0, 14))
        win.protocol("WM_DELETE_WINDOW", close)
        win.grab_set()
        win.wait_window()
        root.destroy()
    except Exception as e:
        print(f"[MOVE HELP DIALOG] {problem} | {e}")


def show_game_over(gs, winner, reason, moves_count, captured=None):
    """
    Beautiful game-over window with the score. `winner` in {"White","Black","Draw"}.
    Returns True to play again, False to quit.
    """
    feat.play_sound("draw" if winner == "Draw" else
                    "win" if winner == "White" else "lose")
    wc, bc, wv, bv = feat.count_material(gs)
    accent = {"White": "#1c74d6", "Black": "#c0392b", "Draw": "#7a7d83"}.get(winner, "#1c74d6")
    banner = {"White": "White wins!",
              "Black": "Computer (Black) wins!",
              "Draw": "Draw"}.get(winner, "Game over")
    result = {"again": False}
    try:
        root = tk.Tk()
        root.withdraw()
        win = tk.Toplevel(root)
        win.title("Game over")
        win.configure(bg="#ffffff")
        win.attributes("-topmost", True)
        win.resizable(False, False)

        tk.Frame(win, bg=accent, height=6, width=420).pack(fill="x")
        tk.Label(win, text=banner, bg="#ffffff", fg=accent,
                 font=("Segoe UI", 22, "bold")).pack(padx=30, pady=(18, 2))
        tk.Label(win, text=f"by {reason}", bg="#ffffff", fg="#666",
                 font=("Segoe UI", 12)).pack(pady=(0, 12))

        card = tk.Frame(win, bg="#f5f6f8", highlightbackground="#dfe3e8",
                        highlightthickness=1)
        card.pack(padx=30, pady=(0, 8), fill="x")
        tk.Label(card, text="SCORE", bg="#f5f6f8", fg="#888",
                 font=("Segoe UI", 10, "bold")).grid(row=0, column=0, columnspan=3,
                                                     sticky="w", padx=12, pady=(8, 2))

        def srow(r, label, w, b):
            tk.Label(card, text=label, bg="#f5f6f8", fg="#333",
                     font=("Segoe UI", 11)).grid(row=r, column=0, sticky="w", padx=12, pady=2)
            tk.Label(card, text=str(w), bg="#f5f6f8", fg="#1c74d6",
                     font=("Segoe UI", 11, "bold")).grid(row=r, column=1, padx=14)
            tk.Label(card, text=str(b), bg="#f5f6f8", fg="#c0392b",
                     font=("Segoe UI", 11, "bold")).grid(row=r, column=2, padx=14)
        tk.Label(card, text="", bg="#f5f6f8").grid(row=1, column=0)
        tk.Label(card, text="White", bg="#f5f6f8", fg="#1c74d6",
                 font=("Segoe UI", 10, "bold")).grid(row=1, column=1)
        tk.Label(card, text="Black", bg="#f5f6f8", fg="#c0392b",
                 font=("Segoe UI", 10, "bold")).grid(row=1, column=2)
        srow(2, "Pieces left", wc, bc)
        srow(3, "Material", wv, bv)
        adv = wv - bv
        tk.Label(card, text=(f"Material edge:  {'White' if adv>0 else 'Black'} +{abs(adv)}"
                             if adv else "Material edge:  even"),
                 bg="#f5f6f8", fg="#b0840f", font=("Segoe UI", 10, "bold")
                 ).grid(row=4, column=0, columnspan=3, sticky="w", padx=12, pady=(2, 8))
        tk.Label(card, text=f"Moves played:  {moves_count}", bg="#f5f6f8", fg="#333",
                 font=("Segoe UI", 10)).grid(row=5, column=0, columnspan=3,
                                            sticky="w", padx=12, pady=(0, 8))

        btns = tk.Frame(win, bg="#ffffff")
        btns.pack(pady=(6, 18))

        def again():
            result["again"] = True
            win.destroy()
        tk.Button(btns, text="Play again", bg="#4C9A2A", fg="white",
                  font=("Segoe UI", 11, "bold"), width=14, command=again
                  ).pack(side="left", padx=8)
        tk.Button(btns, text="Quit", font=("Segoe UI", 11), width=10,
                  command=win.destroy).pack(side="left", padx=8)

        win.grab_set()
        win.wait_window()
        root.destroy()
    except Exception as e:
        print(f"[GAME OVER DIALOG] {winner} | {e}")
    return result["again"]


class RobotHandler:
    """
    Класс для обработки TCP-подключений от робота.
    Работает в фоновом потоке, чтобы не блокировать основной цикл (pygame).
    """
    def __init__(self, host='0.0.0.0', port=3000):
        self.host = host
        self.port = port
        self.client_socket = None
        self.move_requested = False
        self.running = True
        
        # Запускаем сервер в фоновом (daemon) потоке, чтобы он завершался вместе с программой
        self.server_thread = threading.Thread(target=self._server_loop, daemon=True)
        self.server_thread.start()
        
    def _server_loop(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
            # Предотвращает ошибку "Address already in use" при перезапусках
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((self.host, self.port))
            server.listen(1)
            # Таймаут позволяет выходить из accept() и безопасно завершать поток (self.running)
            server.settimeout(1.0)
            print(f"[ROBOT] Сервер запущен, ожидание манипулятора на порту {self.port}...")
            
            while self.running:
                try:
                    conn, addr = server.accept()
                    print(f"[ROBOT] Робот подключен: {addr}")
                    self.client_socket = conn
                    
                    with conn:
                        while self.running:
                            try:
                                # Чтение данных с таймаутом, чтобы цикл не зависал навсегда
                                conn.settimeout(1.0)
                                data = conn.recv(1024)
                                if not data:
                                    break # Соединение разорвано клиентом
                                
                                msg = data.decode('utf-8').strip()
                                if msg == "MOVE":
                                    print("[ROBOT] Получена команда MOVE от робота")
                                    # Устанавливаем флаг, что робот запросил проверку хода
                                    self.move_requested = True
                            except socket.timeout:
                                continue # Истек таймаут чтения (продолжаем слушать)
                            except Exception as e:
                                print(f"[ROBOT] Ошибка при чтении: {e}")
                                break
                    print("[ROBOT] Робот отключен. Ожидаем нового подключения...")
                    self.client_socket = None
                    
                except socket.timeout:
                    continue # Таймаут accept(), возвращаемся к началу цикла
                except Exception as e:
                    if self.running:
                        print(f"[ROBOT] Ошибка сервера: {e}")

    @property
    def connected(self):
        return self.client_socket is not None

    def check_for_move(self):
        """Проверяет флаг запроса от робота и сбрасывает его."""
        if self.move_requested:
            self.move_requested = False
            return True
        return False
        
    def send_move(self, move):
        """Отправляет ответный ход роботу в виде 6 байт."""
        if self.client_socket:
            try:
                import struct

                move_type      = 1
                promotion_type = 0

                if move.contains_castle():
                    # move.castle = (rook_piece, rook_from_sq, rook_to_sq)
                    # rook_to_sq.get_file() > king_start_file  → kingside
                    _, _, rook_to_sq = move.castle
                    if rook_to_sq.get_file() > move.start_square.get_file():
                        move_type = 4  # kingside castling
                    else:
                        move_type = 5  # queenside castling
                elif move.contains_enpassant():
                    move_type = 3
                elif move.piece_captured is not None:
                    move_type = 2

                if move.contains_promotion():
                    promo = move.promotion
                    if   promo == 'Queen':  promotion_type = 1
                    elif promo == 'Rook':   promotion_type = 2
                    elif promo == 'Bishop': promotion_type = 3
                    elif promo == 'Knight': promotion_type = 4

                from_x = move.start_square.get_file() + 1
                from_y = 8 - move.start_square.get_rank()
                to_x   = move.end_square.get_file() + 1
                to_y   = 8 - move.end_square.get_rank()

                if move_type in (4, 5):   # castling — robot handles positioning
                    from_x = from_y = to_x = to_y = 1

                packet = struct.pack(
                    '6B', move_type, from_x, from_y, to_x, to_y, promotion_type)

                print(f"[ROBOT] Sending: type={move_type}, "
                      f"({from_x},{from_y})->({to_x},{to_y}), promo={promotion_type}")
                self.client_socket.sendall(packet)
            except Exception as e:
                print(f"[ROBOT] Error sending move: {e}")
                self.client_socket = None
        else:
            print("[ROBOT] Cannot send move: robot not connected.")

    def close(self):
        """Корректная остановка сервера и сокетов."""
        self.running = False
        if self.client_socket:
            try:
                self.client_socket.close()
            except:
                pass

def main():
    """
    Main game loop with Arduino integration.
    
    WORKFLOW:
    =========
    1. Game starts with WAITING_FOR_HUMAN state (White's turn)
    2. User makes a move on the physical Arduino board
    3. User presses ENTER to scan the board
    4. System compares old vs new board state to detect the move
    5. If valid move detected:
       - Apply move to game state
       - Switch to PROCESSING_AI state
       - AI calculates and executes Black's move
       - Log AI move to console
       - Update GUI to show AI's move
       - Return to WAITING_FOR_HUMAN state
    6. Repeat from step 2
    
    CONTROLS:
    =========
    - ENTER: Scan Arduino board and process move
    - Z: Undo last move (only when waiting for human)
    - Mouse Click: Debug mode - make moves without Arduino
    - ESC/Close: Exit game
    
    ERROR HANDLING:
    ===============
    - Invalid moves are rejected with error message
    - Arduino connection failures are logged
    - AI calculation errors fall back to random moves
    - All exceptions caught to prevent crashes
    """
    global screen, clock, theme, gs, frame, fullscreen

    global BAUD_RATE

    # High-DPI awareness as early as possible (based on the last saved choice).
    try:
        import chess_settings
        apply_dpi_awareness(chess_settings.load().get("dpi_aware", True))
    except Exception:
        pass

    # --- Startup configuration dialog (COM, difficulty, mode, clock, display) ---
    cfg = feat.startup_config(default_port=SERIAL_PORT)
    if not cfg.get("ok"):
        print(">>> Setup cancelled — exiting.")
        return
    humanWhite, humanBlack = cfg["humanWhite"], cfg["humanBlack"]
    chosen_port = cfg["port"]
    robot_mode = cfg["robot_mode"]          # True -> send AI move to UR3
    difficulty = cfg["difficulty"]
    ai.set_difficulty_preset(difficulty)
    BAUD_RATE = int(cfg.get("baud", BAUD_RATE))

    p.init()
    arduino = ArduinoHandler(port=chosen_port)
    robot = RobotHandler(host=cfg.get("robot_host", "0.0.0.0"),
                         port=int(cfg.get("robot_port", 3000)))

    # --- Feature objects ---
    game_log = feat.GameLog()
    captured = feat.CapturedTracker()
    game_moves = feat.MovesHistory()
    game_clock = feat.ChessClock(cfg["base_seconds"], cfg["increment"])
    game_log.add(f"Setup: port={chosen_port}, difficulty={difficulty}, "
                 f"mode={'robot' if robot_mode else 'user'}", "ok")

    # Warn (with guidance) if the Arduino board did not open.
    if not arduino.connected:
        show_error(
            "Arduino board not connected",
            f"Could not open the chess board on {chosen_port}.",
            [
                "Start the board emulator (or connect the real Arduino).",
                f"Check the COM port — '{chosen_port}' must match the emulator's paired port.",
                "On Windows the virtual pair needs com0com (COM20<->COM21).",
                "You can keep playing; press ENTER to retry a board scan.",
            ],
        )

    # Настройка окна (board on the left, feature panel on the right).
    # `screen` is the real display; `frame` is the fixed logical canvas we draw on.
    scale = float(cfg.get("window_scale", 1.0))
    win_w = max(640, int(feat.WINDOW_W * scale))
    win_h = max(480, int(feat.WINDOW_H * scale))
    if cfg.get("start_fullscreen"):
        fullscreen = True
        screen = p.display.set_mode((0, 0), p.FULLSCREEN)
    else:
        fullscreen = False
        screen = p.display.set_mode((win_w, win_h), p.RESIZABLE)
    frame = p.Surface((feat.WINDOW_W, feat.WINDOW_H))
    clock = p.time.Clock()
    p.display.set_caption(CAPTION + "   (F11 fullscreen)")

    # Виртуальный экран для доски
    board_surface = p.Surface((WIDTH, HEIGHT))

    # Black and white theme (hardcoded)
    theme = themes["bw"]
    loadImages()

    def panel_ctx(state_label=None):
        return {
            "clock": game_clock, "log": game_log, "captured": captured,
            "moves": game_moves, "images": IMAGES,
            "arduino_connected": arduino.connected,
            "robot_connected": robot.connected,
            "robot_mode": robot_mode, "difficulty": difficulty,
            "port": chosen_port, "state_label": state_label,
        }

    def reset_game():
        """Reset to the standard starting position and clear history/clocks."""
        global gs
        nonlocal validMoves, previous_board_snapshot, game_state
        gs = chess_engine.GameState()        # standard starting position
        captured.reset()
        game_moves.reset()
        game_clock.times["white"] = game_clock.base
        game_clock.times["black"] = game_clock.base
        game_clock.start("white")
        validMoves = gs.get_valid_moves()
        previous_board_snapshot = get_board_snapshot(gs)
        game_state = WAITING_FOR_HUMAN
        game_log.add("Board reset to the starting position.", "ok")

    # --- ON-SCREEN BUTTON BAR (pause / reset / scan / auto-scan / clear / ...) ---
    ui_state = {"auto_scan": False, "paused": False, "last_auto": 0.0}

    def _post_enter():
        p.event.post(p.event.Event(p.KEYDOWN, {'key': p.K_RETURN, 'mod': p.KMOD_NONE,
                                               'unicode': '\r', 'scancode': p.K_RETURN}))

    def act_scan():
        if game_state == WAITING_FOR_HUMAN and not ui_state["paused"]:
            feat.play_sound("select")
            _post_enter()
        else:
            game_log.add("Scan unavailable (AI thinking or paused).", "warn")

    def act_auto():
        ui_state["auto_scan"] = not ui_state["auto_scan"]
        btn_auto.on = ui_state["auto_scan"]
        game_log.add(f"Auto-scan {'ON (every 1s)' if ui_state['auto_scan'] else 'OFF'}", "ok")

    def act_pause():
        ui_state["paused"] = not ui_state["paused"]
        btn_pause.on = ui_state["paused"]
        btn_pause.label = "Resume" if ui_state["paused"] else "Pause"
        (game_clock.pause if ui_state["paused"] else game_clock.resume)()
        game_log.add("Paused." if ui_state["paused"] else "Resumed.", "warn")

    def act_reset():
        reset_game()
        ui_state["paused"] = False
        btn_pause.on = False
        btn_pause.label = "Pause"

    def act_clear():
        global gs
        nonlocal validMoves, previous_board_snapshot
        gs.force_clear()
        validMoves = gs.get_valid_moves()
        previous_board_snapshot = get_board_snapshot(gs)
        game_log.add("Board cleared to empty (init state).", "warn")

    def act_sync():
        global gs
        nonlocal validMoves, previous_board_snapshot, game_state
        fresh = chess_engine.GameState()
        fresh.force_clear()
        data = arduino.get_board_state()
        if data:
            sync_entire_board(fresh, data)
            gs = fresh
            validMoves = gs.get_valid_moves()
            previous_board_snapshot = get_board_snapshot(gs)
            game_state = WAITING_FOR_HUMAN
            game_log.add("Synced board from the Arduino.", "ok")
        else:
            game_log.add("Sync failed — no board data.", "error")
            feat.play_sound("error")

    def act_undo():
        nonlocal validMoves, previous_board_snapshot
        if game_state == WAITING_FOR_HUMAN and not gs.gameover:
            try:
                gs.undo_move()
                validMoves = gs.get_valid_moves()
                previous_board_snapshot = get_board_snapshot(gs)
                game_log.add("Move undone.", "ok")
            except Exception as e:
                game_log.add(f"Undo failed: {e}", "error")

    btn_scan  = feat.Button("scan", "Scan")
    btn_auto  = feat.Button("auto", "Auto", toggle=True)
    btn_pause = feat.Button("pause", "Pause", toggle=True)
    btn_undo  = feat.Button("undo", "Undo")
    btn_clear = feat.Button("clear", "Clear")
    btn_sync  = feat.Button("sync", "Sync")
    btn_reset = feat.Button("reset", "Reset")
    btn_full  = feat.Button("full", "Full")
    buttons = [btn_scan, btn_auto, btn_pause, btn_undo, btn_clear, btn_sync, btn_reset, btn_full]
    actions = {"scan": act_scan, "auto": act_auto, "pause": act_pause, "undo": act_undo,
               "clear": act_clear, "sync": act_sync, "reset": act_reset,
               "full": toggle_fullscreen}
    feat.layout_buttons(buttons, feat.BOARD_X, feat.BTN_Y, feat.BOARD_SIZE, feat.BTN_H)

    # --- ИНИЦИАЛИЗАЦИЯ ---
    gs = chess_engine.GameState()

    # 1. Clear the board before the Arduino sync.
    gs.force_clear()

    # 2. АВТОМАТИЧЕСКИЙ ЗАПРОС ПРИ СТАРТЕ
    print(">>> Первоначальное сканирование доски...")
    print(">>> ПРИМЕЧАНИЕ: Система поддерживает неполную расстановку фигур.")
    print(">>> Вы можете начать игру с теми фигурами, которые есть в наличии.\n")
    arduino_data = arduino.get_board_state()
    if arduino_data:
        print(">>> Получены данные от Arduino, синхронизируем доску...")
        sync_entire_board(gs, arduino_data)
    else:
        print(">>> Данные от Arduino не получены. Используется пустая доска.")
        # Не делаем ничего, gs уже был очищен ранее и готов к работе
        pass

    validMoves = gs.get_valid_moves()
    squareClicked = () 
    playerClicks = []   
    moveMade = False
    
    # Debug: показываем сколько валидных ходов доступно
    print(f"[DEBUG] Доступно валидных ходов: {len(validMoves)}")
    if len(validMoves) > 0:
        print(f"[DEBUG] Примеры ходов: {[m.get_chess_notation() for m in validMoves[:5]]}")
    else:
        print("[WARNING] Нет доступных ходов! Проверьте, что фигуры расставлены правильно.")
    
    running = True
    # --- ПЕРЕД НАЧАЛОМ ЦИКЛА ---
    # Сохраняем первоначальное состояние доски для сравнения
    previous_board_snapshot = get_board_snapshot(gs)
    
    # --- STATE MACHINE ---
    game_state = WAITING_FOR_HUMAN  # Start waiting for human (White) move
    ai_move_to_execute = None  # Store AI move to execute in next frame
    
    print("\n=== GAME READY ===")
    print("White to move. Press ENTER to scan board after making your move.\n")
    game_log.add("Game ready. White to move.", "white")
    game_clock.start("white" if gs.white_to_move else "black")

    board_x, board_y = feat.BOARD_X, feat.BOARD_Y

    while running:
        # Доска рисуется в фиксированной позиции слева; панель — справа
        game_clock.tick()
        if not ui_state["paused"]:
            game_clock.set_active("white" if gs.white_to_move else "black")

        # --- ПРОВЕРКА КОМАНД ОТ РОБОТА ---
        # Если робот запросил ход, искусственно генерируем событие "нажата кнопка ENTER"
        if game_state == WAITING_FOR_HUMAN and robot.check_for_move():
            _post_enter()

        # --- AUTO-SCAN (каждую секунду, если включено) ---
        if (ui_state["auto_scan"] and game_state == WAITING_FOR_HUMAN
                and not ui_state["paused"]):
            now = time.time()
            if now - ui_state["last_auto"] >= 1.0:
                ui_state["last_auto"] = now
                _post_enter()

        for event in p.event.get():
            if event.type == p.QUIT:
                running = False

            # --- КНОПКИ МЫШИ ---
            elif event.type == p.MOUSEBUTTONDOWN and event.button == 1:
                b = feat.button_at(buttons, frame_pos(event.pos))
                if b and b.key in actions:
                    actions[b.key]()

            # --- РЕСАЙЗ ОКНА ---
            elif event.type == p.VIDEORESIZE and not fullscreen:
                screen = p.display.set_mode((event.w, event.h), p.RESIZABLE)

            # --- FULLSCREEN TOGGLE (F11) ---
            elif event.type == p.KEYDOWN and event.key == p.K_F11:
                toggle_fullscreen()

            # --- EXIT FULLSCREEN (ESC) ---
            elif event.type == p.KEYDOWN and event.key == p.K_ESCAPE and fullscreen:
                toggle_fullscreen()

            # --- КЛАВИАТУРА ---
            elif event.type == p.KEYDOWN:
                # --- СИНХРОНИЗАЦИЯ С ARDUINO ПО НАЖАТИЮ ENTER ---
                if event.key == p.K_RETURN:
                    # Only process ENTER if we're waiting for human move
                    if game_state != WAITING_FOR_HUMAN:
                        print(">>> [ENTER] Игнорируется - ИИ думает или уже обрабатывается ход...")
                        continue
                        
                    print("\n>>> [ENTER] Сканирование доски...")
                    
                    try:
                        # 1. Сохраняем состояние ДО синхронизации
                        old_snapshot = previous_board_snapshot.copy()

                        # 2. Получаем новое состояние от Arduino
                        arduino_data = arduino.get_board_state()
                        
                        if not arduino_data:
                            print(">>> ОШИБКА: Данные от Arduino не получены.")
                            game_log.add("Board scan failed — no data.", "error")
                            show_error(
                                "No data from the board",
                                f"The board scan on {chosen_port} returned no data.",
                                [
                                    "Make sure the board emulator (or Arduino) is running.",
                                    f"Confirm the COM port '{chosen_port}' is correct.",
                                    "Then press ENTER to scan again.",
                                ],
                            )
                            continue
                        
                        # 3. Создаем "снимок" нового состояния по данным Arduino
                        new_board_snapshot = get_board_snapshot_from_data(arduino_data)

                        # 4. Проверяем, изменилась ли доска
                        if old_snapshot == new_board_snapshot:
                            print(">>> Доска не изменилась. Сделайте ход и нажмите ENTER.")
                            continue

                        # 5. Находим ход, который сделал игрок
                        player_move = find_move_from_snapshots(old_snapshot, new_board_snapshot, gs)
                        
                        if not player_move:
                            print(">>> ОШИБКА: Ход не распознан. Попробуйте еще раз.")
                            game_log.add("Move not recognised.", "error")
                            feat.play_sound("error")
                            choice = show_unrecognised(
                                "Could not work out which move was made from the board change.",
                                [
                                    "Make exactly one legal move, then update again.",
                                    "Make sure pieces are squarely on their squares.",
                                    "If the board drifted out of sync, resync the whole board.",
                                ],
                            )
                            if choice == "resync":
                                act_sync()        # re-read the WHOLE board from the Arduino
                            elif choice == "retry":
                                _post_enter()      # scan / update the board again
                            continue
                        
                        print(f"[DEBUG] Обнаружен ход: {player_move.start_square.get_name()} -> {player_move.end_square.get_name()}")
                        print(f"[DEBUG] Всего валидных ходов: {len(validMoves)}")
                        
                        # 6. Ищем соответствующий валидный ход
                        #    For promotions we also check which piece appeared
                        #    on the back rank in the new Arduino snapshot.
                        actual_move_to_make = None
                        for valid_move in validMoves:
                            if (valid_move.start_square == player_move.start_square
                                    and valid_move.end_square == player_move.end_square):
                                if valid_move.contains_promotion():
                                    # Detect which piece the player placed
                                    dest_key = (
                                        player_move.end_square.get_file(),
                                        player_move.end_square.get_rank(),
                                    )
                                    dest_sig = new_board_snapshot.get(dest_key, '')
                                    if valid_move.promotion in dest_sig:
                                        actual_move_to_make = valid_move
                                        break
                                    # keep looping to try other promotion types
                                else:
                                    actual_move_to_make = valid_move
                                    break

                        # Fallback: if promotion piece type not recognised in
                        # snapshot, try to find Queen promotion first, then pick the first match.
                        if not actual_move_to_make:
                            for valid_move in validMoves:
                                if (valid_move.start_square == player_move.start_square
                                        and valid_move.end_square == player_move.end_square):
                                    if valid_move.contains_promotion():
                                        if valid_move.promotion == 'Queen':
                                            actual_move_to_make = valid_move
                                            break
                            if not actual_move_to_make:
                                for valid_move in validMoves:
                                    if (valid_move.start_square == player_move.start_square
                                            and valid_move.end_square == player_move.end_square):
                                        actual_move_to_make = valid_move
                                        break
                        
                        if not actual_move_to_make:
                            # Безопасный вывод информации о невалидном ходе
                            try:
                                move_desc = f"от {player_move.start_square.get_name()} к {player_move.end_square.get_name()}"
                            except:
                                move_desc = "(координаты недоступны)"
                            
                            print(f">>> ОШИБКА: Невалидный ход обнаружен: {move_desc}")
                            print(">>> Причины: ход может быть незаконным, или фигура не может так ходить.")
                            
                            # Показываем доступные ходы с этой клетки
                            piece_on_square = player_move.start_square.get_piece()
                            if piece_on_square:
                                print(f">>> Фигура на клетке: {piece_on_square.get_color()} {piece_on_square.get_name()}")
                                available_from_square = [m for m in validMoves if m.start_square == player_move.start_square]
                                if available_from_square:
                                    print(f">>> Доступные ходы с {player_move.start_square.get_name()}: {[m.end_square.get_name() for m in available_from_square]}")
                                else:
                                    print(f">>> С {player_move.start_square.get_name()} нет доступных ходов!")
                            else:
                                print(f">>> На клетке {player_move.start_square.get_name()} нет фигуры в игровом состоянии!")

                            print(">>> Верните фигуру на место и попробуйте другой ход.")

                            # --- popup: illegal move / wrong figure + what to do ---
                            try:
                                src = player_move.start_square.get_name()
                                dst = player_move.end_square.get_name()
                            except Exception:
                                src, dst = "?", "?"
                            legal_here = [m.end_square.get_name() for m in validMoves
                                          if m.start_square == player_move.start_square]
                            advice = ["Put the moved piece back where it was."]
                            if piece_on_square is None:
                                advice.append(f"There is no piece on {src} in the game — "
                                              f"re-check the board, or press R to reset.")
                            elif legal_here:
                                advice.append(f"That piece can legally go to: {', '.join(legal_here)}.")
                            else:
                                advice.append(f"The piece on {src} has no legal moves — move a different one.")
                            advice.append("Then press ENTER to scan again.")
                            game_log.add(f"Illegal move {src}->{dst}", "error")
                            feat.play_sound("error")
                            show_move_help(
                                f"The move {src} → {dst} is not legal in this position.",
                                advice, validMoves, attempted=(src, dst), gs=gs)
                            continue
                        
                        # 7. Применяем ход игрока
                        feat.play_sound("capture" if actual_move_to_make.piece_captured
                                        else "move")
                        captured.add_from_move(actual_move_to_make)
                        gs.make_new_move(actual_move_to_make)
                        try:
                            notation = actual_move_to_make.get_chess_notation(gs)
                        except Exception:
                            notation = "move"
                        print(f">>> ✓ Ход белых: {notation}")
                        game_log.add(f"White: {notation}", "white")
                        game_moves.add("white", notation)
                        game_clock.add_increment("white")
                        animate_move(screen, board_surface, gs, actual_move_to_make,
                                     theme, panel_ctx("Black (AI) thinking…"),
                                     (board_x, board_y))

                        # 8. Обновляем доску и валидные ходы
                        validMoves = gs.get_valid_moves()
                        previous_board_snapshot = new_board_snapshot.copy()
                        
                        # 9. Проверяем конец игры
                        if gs.checkmate or gs.stalemate:
                            if gs.checkmate:
                                msg = "МАТ! Белые победили!"
                            else:
                                msg = "ПАТ! Ничья!"
                            print(f"\n=== {msg} ===")
                            gs.gameover = True

                            # Красивое окно с результатом и счётом
                            _winner = "White" if gs.checkmate else "Draw"
                            _reason = "Checkmate" if gs.checkmate else "Stalemate"
                            if show_game_over(gs, _winner, _reason, len(game_moves.rows), captured):
                                gs = chess_engine.GameState()
                                gs.force_clear()
                                print(">>> Начинаем новую игру (синхронизация доски)...")
                                captured.reset()
                                game_clock = feat.ChessClock(cfg["base_seconds"], cfg["increment"])
                                game_clock.start("white")
                                game_log.add("New game started.", "ok")
                                game_moves.reset()
                                arduino_data = arduino.get_board_state()
                                if arduino_data:
                                    sync_entire_board(gs, arduino_data)
                                validMoves = gs.get_valid_moves()
                                previous_board_snapshot = get_board_snapshot(gs)
                                game_state = WAITING_FOR_HUMAN
                            else:
                                running = False
                            continue
                        
                        # 10. Переключаемся в режим обработки хода ИИ
                        game_state = PROCESSING_AI
                        print(">>> Черные думают...")
                        
                    except Exception as e:
                        print(f">>> КРИТИЧЕСКАЯ ОШИБКА при обработке хода: {e}")
                        import traceback
                        traceback.print_exc()
                        game_state = WAITING_FOR_HUMAN  # Возврат в состояние ожидания

            # Mouse click functionality removed - Arduino-only mode
            
            # --- ОТМЕНА ХОДА ---
            elif event.type == p.KEYDOWN and event.key == p.K_z:
                if game_state == WAITING_FOR_HUMAN and not gs.gameover:
                    try:
                        gs.undo_move()
                        validMoves = gs.get_valid_moves()
                        previous_board_snapshot = get_board_snapshot(gs)
                        print(">>> Ход отменен.")
                    except Exception as e:
                        print(f">>> Ошибка при отмене хода: {e}")
                else:
                    print(">>> Отмена хода недоступна в данный момент.")

            # --- DIFFICULTY CYCLE (D) ---
            elif event.type == p.KEYDOWN and event.key == p.K_d:
                names = list(ai.DIFFICULTY_PRESETS)
                difficulty = names[(names.index(difficulty) + 1) % len(names)]
                ai.set_difficulty_preset(difficulty)
                game_log.add(f"Difficulty -> {difficulty}", "ai")

            # --- TOGGLE ROBOT / USER MOVE (M) ---
            elif event.type == p.KEYDOWN and event.key == p.K_m:
                robot_mode = not robot_mode
                game_log.add(f"Black moved by -> {'UR3 robot' if robot_mode else 'user'}",
                             "robot")

            # --- RESET BOARD (R) ---
            elif event.type == p.KEYDOWN and event.key == p.K_r:
                if game_state == WAITING_FOR_HUMAN:
                    reset_game()
                else:
                    game_log.add("Reset ignored while AI is thinking.", "warn")

        # --- AI MOVE PROCESSING ---
        # Execute AI move when in PROCESSING_AI state
        if game_state == PROCESSING_AI and not gs.gameover:
            try:
                # 1. Calculate AI move
                if len(validMoves) == 0:
                    print(">>> Нет доступных ходов для черных.")
                    if gs.checkmate or gs.stalemate:
                        if gs.checkmate:
                            msg = "МАТ! Белые победили!"
                        else:
                            msg = "ПАТ! Ничья!"
                        print(f"\n=== {msg} ===")
                        gs.gameover = True
                        game_state = WAITING_FOR_HUMAN

                        _winner = "White" if gs.checkmate else "Draw"
                        _reason = "Checkmate" if gs.checkmate else "Stalemate"
                        if show_game_over(gs, _winner, _reason, len(game_moves.rows), captured):
                            gs = chess_engine.GameState()
                            gs.force_clear()
                            print(">>> Начинаем новую игру (синхронизация доски)...")
                            captured.reset()
                            game_clock = feat.ChessClock(cfg["base_seconds"], cfg["increment"])
                            game_clock.start("white")
                            game_log.add("New game started.", "ok")
                            game_moves.reset()
                            arduino_data = arduino.get_board_state()
                            if arduino_data:
                                sync_entire_board(gs, arduino_data)
                            validMoves = gs.get_valid_moves()
                            previous_board_snapshot = get_board_snapshot(gs)
                            game_state = WAITING_FOR_HUMAN
                        else:
                            running = False
                else:
                    AIMove = ai.getBestMove(gs)
                    
                    # 2. Fallback to random move if AI fails
                    if AIMove is None:
                        print(">>> ИИ не смог найти лучший ход, выбирается случайный...")
                        AIMove = ai.getRandomMove(validMoves)
                    
                    # 3. Execute AI move
                    if AIMove is not None:
                        feat.play_sound("capture" if AIMove.piece_captured else "move")
                        captured.add_from_move(AIMove)
                        gs.make_new_move(AIMove)

                        # ---> ОТПРАВКА ХОДА РОБОТУ (только в режиме робота) <---
                        if robot_mode:
                            if robot.connected:
                                robot.send_move(AIMove)
                            else:
                                show_error(
                                    "Robot not connected",
                                    "Robot mode is on, but no UR3 robot is connected, "
                                    "so the move was not sent.",
                                    [
                                        "Start the emulator / connect the UR3 and let it "
                                        "connect to this PC on port 3000.",
                                        "Or press M to switch to 'User moves black' mode.",
                                        "The move was still applied on screen.",
                                    ],
                                )
                        else:
                            game_log.add("User mode: move NOT sent to robot", "warn")

                        # 4. Log the move
                        try:
                            move_notation = AIMove.get_chess_notation(gs)
                            print(f">>> ✓ Ход черных: {move_notation}")
                            print(f">>>   От {AIMove.start_square.get_name()} к {AIMove.end_square.get_name()}")
                            game_log.add(f"Black (AI): {move_notation}", "ai")
                            game_moves.add("black", move_notation)
                        except Exception as e:
                            print(f">>> ✓ Ход черных выполнен (ошибка при получении нотации: {e})")
                            game_moves.add("black", "…")
                        game_clock.add_increment("black")
                        animate_move(screen, board_surface, gs, AIMove, theme,
                                     panel_ctx("White to move"), (board_x, board_y))

                        # 5. Update valid moves
                        validMoves = gs.get_valid_moves()
                        
                        # 6. Update board snapshot after AI move
                        previous_board_snapshot = get_board_snapshot(gs)
                        
                        # 7. Check for game over
                        if gs.checkmate or gs.stalemate:
                            if gs.checkmate:
                                msg = "МАТ! Черные победили!"
                            else:
                                msg = "ПАТ! Ничья!"
                            print(f"\n=== {msg} ===")
                            gs.gameover = True

                            _winner = "Black" if gs.checkmate else "Draw"
                            _reason = "Checkmate" if gs.checkmate else "Stalemate"
                            if show_game_over(gs, _winner, _reason, len(game_moves.rows), captured):
                                gs = chess_engine.GameState()
                                gs.force_clear()
                                print(">>> Начинаем новую игру (синхронизация доски)...")
                                captured.reset()
                                game_clock = feat.ChessClock(cfg["base_seconds"], cfg["increment"])
                                game_clock.start("white")
                                game_log.add("New game started.", "ok")
                                game_moves.reset()
                                arduino_data = arduino.get_board_state()
                                if arduino_data:
                                    sync_entire_board(gs, arduino_data)
                                validMoves = gs.get_valid_moves()
                                previous_board_snapshot = get_board_snapshot(gs)
                                game_state = WAITING_FOR_HUMAN
                            else:
                                running = False
                        else:
                            print("\n>>> Белые, ваш ход. Нажмите ENTER после хода на физической доске.")
                        
                        # 8. Return to waiting state
                        game_state = WAITING_FOR_HUMAN
                    else:
                        print(">>> ОШИБКА: ИИ не смог сгенерировать ход.")
                        game_state = WAITING_FOR_HUMAN
                        
            except Exception as e:
                print(f">>> КРИТИЧЕСКАЯ ОШИБКА при обработке хода ИИ: {e}")
                import traceback
                traceback.print_exc()
                game_state = WAITING_FOR_HUMAN  # Возврат к ожиданию игрока

        # --- ОТРИСОВКА (на логический frame, затем present со скейлом) ---
        frame.fill(BACKGROUND_COLOR)

        # Рисуем доску на board_surface
        board_surface.fill(p.Color(28, 28, 28))
        drawGameState(board_surface, gs, validMoves, squareClicked, theme)
        frame.blit(board_surface, (board_x, board_y))

        # Верхняя панель + боковая панель с функциями
        state_label = ("Black (AI) thinking…" if game_state == PROCESSING_AI
                       else "White to move")
        ctx = panel_ctx(state_label)
        try:
            feat.draw_top_bar(frame, gs, ctx)
            feat.draw_panel(frame, gs, ctx)
            # button bar under the board + hint line
            hover = frame_pos(p.mouse.get_pos())
            feat.draw_buttons(frame, buttons, hover)
            hint = ("Click buttons or keys:  D=difficulty M=robot/user "
                    "Z=undo ENTER=scan F11=fullscreen   • sound on")
            frame.blit(feat.font(11).render(hint, True, feat.C_DIM),
                       (feat.BOARD_X, feat.BTN_Y + feat.BTN_H + 6))
        except Exception as e:
            print(f"[UI] panel draw error: {e}")

        clock.tick(MAX_FPS)
        present()
        
    arduino.close()
    robot.close()
    p.quit()
    sys.exit()

# --- ФУНКЦИИ ОТРИСОВКИ ---
def loadImages():
    # Use absolute path based on the script's directory so images are found
    # regardless of the current working directory.
    script_dir = os.path.dirname(os.path.abspath(__file__))
    images_dir = os.path.join(script_dir, 'images')
    pieces = ['wP', 'wR', 'wN', 'wB', 'wQ', 'wK', 'bP', 'bR', 'bN', 'bB', 'bQ', 'bK']
    for piece in pieces:
        img_path = os.path.join(images_dir, piece + '.png')
        try:
            IMAGES[piece] = p.transform.smoothscale(
                p.image.load(img_path), (SQ_SIZE, SQ_SIZE))
        except Exception as e:
            print(f"[WARNING] Could not load image {img_path}: {e}")

def drawGameState(surface, gs, validMoves, selectedSquare, theme):
    drawBoard(surface, gs, theme, selectedSquare)
    drawPieces(surface, gs)
    # Если есть мат/пат - можно отрисовать текст тут

def drawBoard(surface, gs, theme, selectedSquare):
    for r in range(DIMENSION):
        for c in range(DIMENSION):
            # Определение цвета клетки
            color = theme[0] if ((r+c)%2 == 0) else theme[1]
            
            # Подсветка выделения
            if selectedSquare == (c, r):
                color = theme[2] if ((r+c)%2 == 0) else theme[3]
                
            p.draw.rect(surface, color, p.Rect(c*SQ_SIZE, r*SQ_SIZE, SQ_SIZE, SQ_SIZE))

def drawPieces(surface, gs):
    for r in range(DIMENSION):
        for c in range(DIMENSION):
            piece = gs.board.squares[c, r].piece
            if piece is not None:
                # Получаем имя для картинки (например 'wP')
                # Ваш метод может называться get_image_name() или get_name()
                if hasattr(piece, 'get_image_name'):
                    name = piece.get_image_name()
                else:
                    # Фоллбек, если метода нет
                    name = piece.get_color()[0] + piece.get_name()[0] # w + P
                
                if name in IMAGES:
                    surface.blit(IMAGES[name], p.Rect(c*SQ_SIZE, r*SQ_SIZE, SQ_SIZE, SQ_SIZE))

def animate_move(screen, board_surface, gs, move, theme, panel_ctx, board_pos,
                 frames_per_sq=5):
    """Slide the moved piece from its start square to its end square."""
    try:
        c0, r0 = move.start_square.get_file(), move.start_square.get_rank()
        c1, r1 = move.end_square.get_file(),   move.end_square.get_rank()
        piece = move.piece_moved
        name = (piece.get_image_name() if hasattr(piece, "get_image_name")
                else piece.get_color()[0] + piece.get_name()[0])
        img = IMAGES.get(name)
    except Exception:
        return
    if img is None:
        return
    dc, dr = c1 - c0, r1 - r0
    dist = max(abs(dc), abs(dr)) or 1
    frames = max(1, int(dist * frames_per_sq))
    bx, by = board_pos
    base_color = theme[0] if ((c1 + r1) % 2 == 0) else theme[1]
    for f in range(frames + 1):
        t = f / frames
        cc, rr = c0 + dc * t, r0 + dr * t
        board_surface.fill(p.Color(28, 28, 28))
        drawBoard(board_surface, gs, theme, ())
        drawPieces(board_surface, gs)
        # erase the piece sitting on the destination, then draw it mid-slide
        p.draw.rect(board_surface, base_color,
                    p.Rect(c1 * SQ_SIZE, r1 * SQ_SIZE, SQ_SIZE, SQ_SIZE))
        board_surface.blit(img, p.Rect(int(cc * SQ_SIZE), int(rr * SQ_SIZE),
                                       SQ_SIZE, SQ_SIZE))
        frame.fill(BACKGROUND_COLOR)
        frame.blit(board_surface, (bx, by))
        try:
            feat.draw_top_bar(frame, gs, panel_ctx)
            feat.draw_panel(frame, gs, panel_ctx)
        except Exception:
            pass
        present()
        clock.tick(MAX_FPS)


def printMove(move):
    """Print move information to the console safely."""
    if move is None or move.piece_moved is None:
        return

    number = ''
    if move.piece_moved.get_color() == 'white':
        number = str(move.move_number // 2 + 1) + '. '

    try:
        move_text = move.get_chess_notation()
    except Exception:
        move_text = 'Move'

    print(''.join([number, move_text]), end=' ')

if __name__ == '__main__':
    main()