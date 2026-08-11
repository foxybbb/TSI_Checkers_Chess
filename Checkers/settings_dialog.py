# -*- coding: utf-8 -*-
"""
Startup settings window for the Checkers game.

Shown before the main window so the operator can pick display scale, window
size, high-DPI behaviour and the board / robot connection parameters. Returns
the chosen settings dict (already saved to disk) or None if cancelled.
"""

import tkinter as tk
from tkinter import ttk, messagebox

import settings_store

BG = "#20232a"
CARD = "#2c313c"
ACCENT = "#d2b48c"
TXT = "#f3ece4"
SUBTXT = "#a9b0bd"


def list_serial_ports():
    """Best-effort list of available COM ports (empty if pyserial missing)."""
    try:
        from serial.tools import list_ports
        return [p.device for p in list_ports.comports()]
    except Exception:
        return []


class SettingsDialog:
    def __init__(self, settings: dict):
        self.result = None
        self.s = dict(settings)

        self.root = tk.Tk()
        self.root.title("Checkers — Setup")
        self.root.configure(bg=BG)
        self.root.resizable(False, False)

        # A modest scaling so the setup window itself is readable on hi-dpi.
        try:
            self.root.tk.call("tk", "scaling", max(1.0, float(self.s["ui_scale"])))
        except Exception:
            pass

        self._build()

        self.root.update_idletasks()
        w = self.root.winfo_width()
        h = self.root.winfo_height()
        x = (self.root.winfo_screenwidth() - w) // 2
        y = (self.root.winfo_screenheight() - h) // 3
        self.root.geometry(f"+{x}+{y}")
        self.root.protocol("WM_DELETE_WINDOW", self._cancel)
        self.root.bind("<Return>", lambda e: self._start())
        self.root.bind("<Escape>", lambda e: self._cancel())

    # ── layout helpers ──
    def _section(self, parent, title):
        lf = tk.LabelFrame(parent, text=title, bg=CARD, fg=ACCENT,
                           font=("Segoe UI", 10, "bold"), padx=12, pady=10,
                           labelanchor="nw", bd=1, relief="solid")
        lf.pack(fill="x", padx=16, pady=(0, 12))
        return lf

    def _row(self, parent, label):
        row = tk.Frame(parent, bg=CARD)
        row.pack(fill="x", pady=3)
        tk.Label(row, text=label, bg=CARD, fg=TXT, font=("Segoe UI", 10),
                 width=18, anchor="w").pack(side="left")
        return row

    def _build(self):
        tk.Label(self.root, text="Checkers — Robotic Board", bg=BG, fg=TXT,
                 font=("Segoe UI", 18, "bold")).pack(pady=(18, 2))
        tk.Label(self.root, text="Configure the display and hardware before you start",
                 bg=BG, fg=SUBTXT, font=("Segoe UI", 10)).pack(pady=(0, 14))

        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True)

        # Two columns
        left = tk.Frame(body, bg=BG)
        left.pack(side="left", fill="both", expand=True)
        right = tk.Frame(body, bg=BG)
        right.pack(side="left", fill="both", expand=True)

        # ── Display section ──
        disp = self._section(left, "Display  /  Scaling")

        r = self._row(disp, "Board square size")
        self.cell_var = tk.IntVar(value=int(self.s["cell_size"]))
        tk.Spinbox(r, from_=40, to=160, increment=4, width=7,
                   textvariable=self.cell_var).pack(side="left")
        tk.Label(r, text="px  (whole board = 8×)", bg=CARD, fg=SUBTXT,
                 font=("Segoe UI", 9)).pack(side="left", padx=6)

        r = self._row(disp, "UI scale")
        self.uiscale_var = tk.DoubleVar(value=float(self.s["ui_scale"]))
        tk.Spinbox(r, from_=0.75, to=2.5, increment=0.25, width=7,
                   textvariable=self.uiscale_var, format="%.2f").pack(side="left")
        tk.Label(r, text="× fonts & widgets", bg=CARD, fg=SUBTXT,
                 font=("Segoe UI", 9)).pack(side="left", padx=6)

        self.dpi_var = tk.BooleanVar(value=bool(self.s["dpi_aware"]))
        tk.Checkbutton(disp, text="High-DPI aware (recommended for 4K screens)",
                       variable=self.dpi_var, bg=CARD, fg=TXT, selectcolor=CARD,
                       activebackground=CARD, activeforeground=TXT,
                       font=("Segoe UI", 10)).pack(anchor="w", pady=(6, 0))

        self.fs_var = tk.BooleanVar(value=bool(self.s["start_fullscreen"]))
        tk.Checkbutton(disp, text="Start in full-screen (F11 toggles later)",
                       variable=self.fs_var, bg=CARD, fg=TXT, selectcolor=CARD,
                       activebackground=CARD, activeforeground=TXT,
                       font=("Segoe UI", 10)).pack(anchor="w")

        r = self._row(disp, "Theme")
        self.theme_var = tk.StringVar(value=self.s["theme"])
        ttk.Combobox(r, textvariable=self.theme_var, width=10, state="readonly",
                     values=["Wood", "Dark", "Slate"]).pack(side="left")

        # ── Board connection ──
        conn = self._section(right, "Sensor Board")

        r = self._row(conn, "Connection")
        self.mode_var = tk.StringVar(value=self.s["connection_mode"])
        tk.Radiobutton(r, text="Serial (COM)", variable=self.mode_var, value="serial",
                       bg=CARD, fg=TXT, selectcolor=CARD, activebackground=CARD,
                       activeforeground=TXT, command=self._sync_mode).pack(side="left")
        tk.Radiobutton(r, text="TCP bridge", variable=self.mode_var, value="tcp",
                       bg=CARD, fg=TXT, selectcolor=CARD, activebackground=CARD,
                       activeforeground=TXT, command=self._sync_mode).pack(side="left")

        r = self._row(conn, "COM port")
        self.port_var = tk.StringVar(value=self.s["serial_port"])
        ports = list_serial_ports()
        self.port_combo = ttk.Combobox(r, textvariable=self.port_var, width=12,
                                       values=ports)
        self.port_combo.pack(side="left")
        tk.Button(r, text="Refresh", command=self._refresh_ports).pack(side="left", padx=4)

        r = self._row(conn, "Baud")
        self.baud_var = tk.IntVar(value=int(self.s["baud"]))
        ttk.Combobox(r, textvariable=self.baud_var, width=10,
                     values=[9600, 19200, 38400, 57600, 115200]).pack(side="left")

        r = self._row(conn, "TCP target")
        self.tcp_var = tk.StringVar(value=self.s["tcp_target"])
        self.tcp_entry = tk.Entry(r, textvariable=self.tcp_var, width=18)
        self.tcp_entry.pack(side="left")
        tk.Label(r, text="host:port", bg=CARD, fg=SUBTXT,
                 font=("Segoe UI", 9)).pack(side="left", padx=4)

        # ── Robot connection ──
        robot = self._section(right, "UR3 Robot")
        r = self._row(robot, "Listen host")
        self.rhost_var = tk.StringVar(value=self.s["robot_host"])
        tk.Entry(r, textvariable=self.rhost_var, width=16).pack(side="left")
        r = self._row(robot, "Listen port")
        self.rport_var = tk.IntVar(value=int(self.s["robot_port"]))
        tk.Entry(r, textvariable=self.rport_var, width=8).pack(side="left")
        tk.Label(robot, text="The robot connects to this PC on the port above.",
                 bg=CARD, fg=SUBTXT, font=("Segoe UI", 9),
                 wraplength=260, justify="left").pack(anchor="w", pady=(4, 0))

        # ── Buttons ──
        bar = tk.Frame(self.root, bg=BG)
        bar.pack(fill="x", padx=16, pady=(4, 16))
        tk.Button(bar, text="Cancel", font=("Segoe UI", 10), width=10,
                  command=self._cancel).pack(side="right", padx=(8, 0))
        tk.Button(bar, text="Start Game", bg=ACCENT, fg="#20232a",
                  font=("Segoe UI", 11, "bold"), width=16, relief="flat",
                  cursor="hand2", command=self._start).pack(side="right")
        tk.Button(bar, text="Restore defaults", font=("Segoe UI", 9),
                  command=self._defaults).pack(side="left")

        self._sync_mode()

    # ── behaviour ──
    def _sync_mode(self):
        tcp = self.mode_var.get() == "tcp"
        self.tcp_entry.configure(state="normal" if tcp else "disabled")
        self.port_combo.configure(state="disabled" if tcp else "normal")

    def _refresh_ports(self):
        ports = list_serial_ports()
        self.port_combo.configure(values=ports)
        if ports and self.port_var.get() not in ports:
            self.port_var.set(ports[0])

    def _defaults(self):
        d = settings_store.DEFAULTS
        self.cell_var.set(d["cell_size"])
        self.uiscale_var.set(d["ui_scale"])
        self.dpi_var.set(d["dpi_aware"])
        self.fs_var.set(d["start_fullscreen"])
        self.theme_var.set(d["theme"])
        self.mode_var.set(d["connection_mode"])
        self.port_var.set(d["serial_port"])
        self.baud_var.set(d["baud"])
        self.tcp_var.set(d["tcp_target"])
        self.rhost_var.set(d["robot_host"])
        self.rport_var.set(d["robot_port"])
        self._sync_mode()

    def _collect(self):
        try:
            s = dict(self.s)
            s["cell_size"] = int(self.cell_var.get())
            s["ui_scale"] = round(float(self.uiscale_var.get()), 2)
            s["dpi_aware"] = bool(self.dpi_var.get())
            s["start_fullscreen"] = bool(self.fs_var.get())
            s["theme"] = self.theme_var.get()
            s["connection_mode"] = self.mode_var.get()
            s["serial_port"] = self.port_var.get().strip() or "COM20"
            s["baud"] = int(self.baud_var.get())
            s["tcp_target"] = self.tcp_var.get().strip() or "127.0.0.1:5006"
            s["robot_host"] = self.rhost_var.get().strip() or "0.0.0.0"
            s["robot_port"] = int(self.rport_var.get())
        except (ValueError, tk.TclError) as e:
            messagebox.showerror("Invalid setting",
                                 f"Please check the numeric fields.\n\n{e}")
            return None
        return s

    def _start(self):
        s = self._collect()
        if s is None:
            return
        settings_store.save(s)
        self.result = s
        self.root.destroy()

    def _cancel(self):
        self.result = None
        self.root.destroy()

    def run(self):
        self.root.mainloop()
        return self.result


def ask_settings(settings: dict):
    """Show the setup dialog; return the chosen settings dict or None."""
    return SettingsDialog(settings).run()
