# Robotic Board Games — Checkers & Chess

Play **checkers (draughts)** or **chess** against the computer on a *physical*
board: an Arduino senses the pieces, and a **UR3 robot arm** makes the machine's
moves. This repository bundles both games behind one launcher, with a hardware
emulator so you can develop and test without the real board or robot.

```
Chekers/
├── run.bat            ← start here (Windows): choose Checkers or Chess
├── launcher.py        ← the same launcher, cross-platform
├── Checkers/          ← the checkers game
│   ├── run.bat        ← run checkers directly
│   └── main.py
├── Chess/             ← the chess game
│   └── Chess_main.bat ← run chess directly
└── rfid_robot_board_emulator/  ← board + robot emulator for testing
```

## Quick start

1. **Install Python 3.9+** (3.10–3.13 all work).
2. Double-click **`run.bat`** (or run `python launcher.py`).
3. Pick **Checkers** or **Chess**.
4. Each game opens a **setup window** first — set the display scale, DPI,
   full-screen, and the board/robot connection, then press **Start Game**.

### Install dependencies (first run)

```bat
:: Checkers
cd Checkers
python -m pip install -r requirements.txt

:: Chess
cd ..\Chess
python -m pip install -r req.txt
```

## Checkers — features

The checkers app is built for real hardware use:

| Feature | How |
| --- | --- |
| **Setup window** | Board square size, UI scale, high-DPI, theme, full-screen, COM port / TCP bridge, robot host/port — all before the game starts, and saved to `checkers_settings.json`. |
| **High-DPI screens** | Per-monitor DPI awareness + adjustable UI scale and board size. |
| **Full-screen** | `F11` toggles, `Esc` exits, or use the toolbar / View menu. |
| **Undo / Redo** | Toolbar & side-panel buttons, `Ctrl+Z` / `Ctrl+Y`. |
| **Zoom** | `Ctrl +` / `Ctrl -` or View menu to resize the boards live. |
| **Connection safety** | Live **Board** and **Robot** lamps, status bar warnings on disconnect, auto-reconnect for the robot, and clear "what to do" error dialogs. |
| **Menubar + toolbar + status bar** | Restyled UI with three selectable themes (Wood / Dark / Slate). |

### Connecting the board

- **Serial:** pick the COM port that is the paired end of the board (real
  Arduino, or the emulator via a com0com null-modem pair).
- **TCP bridge:** choose *TCP bridge* in setup and give `host:port` (e.g.
  `127.0.0.1:5006`); no virtual-serial driver needed. This sets the `BOARD_TCP`
  environment variable for the game.

### Connecting the robot

The game **hosts** a TCP server (default port `3000`); the UR3 robot connects to
this PC. The robot status lamp turns green when it connects. If it drops, the
game keeps listening so the robot can reconnect at any time.

## Testing without hardware

Use the emulator in `rfid_robot_board_emulator/` to simulate the sensor board
and the robot. See that folder's `README.md`. In short: start the emulator in
*Checkers* mode, start its board server, then point the game at the matching COM
port (or use the TCP bridge).

## Keyboard shortcuts (Checkers)

| Key | Action |
| --- | --- |
| `F5` | Refresh memory board from the sensor board |
| `Ctrl+Z` / `Ctrl+Y` | Undo / Redo |
| `Ctrl+N` | New / reset board |
| `Ctrl +` / `Ctrl -` | Zoom in / out |
| `F11` | Toggle full-screen |
| `Esc` | Exit full-screen |

## Notes

- `Checkers/` and `Chess/` each retain their own upstream git history; this
  top-level repository ties them together with the launcher and shared docs.
- Board reading and the robot protocol are documented in
  `Chess/UR3_robot_comand_sender.txt` and the emulator README.
