# Chess Board + UR3 Robot Emulator

A small Tkinter test bench that replaces the **physical hardware** of the chess
robot, so the whole system can be exercised with no real board and no real UR3.

It exposes two independent interfaces around one shared on-screen board:

| Interface            | What it emulates              | Transport                                   |
|----------------------|-------------------------------|---------------------------------------------|
| **Board** (Arduino)  | The 64-square sensor board    | **com0com virtual COM** (default) or TCP    |
| **Robot socket**     | The UR3 arm / its host        | TCP **server** (default, robot connects) or client |

## Protocol (matches the project whiteboard)

Robot move = 6 bytes, coords in `[1..8]`:

```
struct '6B' = ( type , x0 , y0 , x1 , y1 , promotion )
x = col + 1      y = 8 - row
type:  1 = normal move   2 = capture   3 = en-passant
       4 = short castle   5 = long castle      (castle sends coords = 1)
promotion: 0 none · 1 Queen · 2 Rook · 3 Bishop · 4 Knight
```

Robot host (from the whiteboard): `192.168.0.100 : 3000`.

Board scan (Arduino) = write `a`, read back 64 space-separated ints + `\r\n`,
`index = row*8 + col`, row 0 = rank 8. Cell `0` = empty, else
`(type_id<<1)|color_bit`, white `color_bit = 1`; type_id Pawn 2 Rook 3 Knight 4
Bishop 5 Queen 6 King 7.

## Install

```powershell
pip install -r requirements.txt          # pyserial
```

Then install **com0com** once (free virtual COM driver, needs admin):
https://com0com.sourceforge.net/

## The virtual COM port (com0com)

`chess_main.py` opens a **real** COM port (`SERIAL_PORT = 'COM5'`) with pyserial.
A real port can't be conjured from Python, so we use com0com to make a
*null-modem pair* — two linked COM ports. The emulator drives one end as the
Arduino; chess_main opens the other end unchanged.

1. Click **Setup com0com** in the emulator. It finds `setupc.exe` and creates a
   **COM5 ⇄ COM6** pair (or reuses an existing pair), then fills in the port.
   * `COM5` → the end **chess_main** opens (already its default `SERIAL_PORT`).
   * `COM6` → the end the **emulator** opens.
2. Click **Start** on the Board panel — the lamp turns green and it serves the
   board state. **No change to chess_main is needed.**

> Creating the pair needs administrator rights. If com0com isn't installed the
> button explains what to do. You can also create the pair manually in the
> com0com "Setup" GUI and just pick the emulator's port.

### No-driver alternative — TCP bridge

If you can't install com0com, switch the Board panel to **TCP bridge**. Then
make `chess_main` read the board over TCP using the bundled adapter
[`..\Chess\board_tcp_serial.py`](../Chess/board_tcp_serial.py) — set one env var,
no code edit:

```powershell
$env:BOARD_TCP = "127.0.0.1:5005"
python chess_main.py
```

…and have `ArduinoHandler` build its serial via `board_tcp_serial.open_board(
SERIAL_PORT, BAUD_RATE)` (a 1-line swap). With `BOARD_TCP` unset it falls back
to the real COM port, so the change is harmless.

## Robot socket

* **Server** (default, matches the whiteboard): the emulator hosts `:3000`; the
  UR3 robot connects to it. **Every move you make on the board is sent to the
  robot** as a 6-byte packet (a GUI version of `..\Chess\UR3_robot_comand_sender.txt`).
* **Client**: the emulator instead connects to `chess_main`'s server as the
  robot, presses **Send MOVE** to ask for a board scan, and applies the move
  packets it gets back (use this to test `chess_main` end-to-end).

## Run

```powershell
python emulator.py        # or double-click run_emulator.bat
```

### Typical session — drive a real robot (Server mode)

1. Board panel → **Setup com0com** → **Start** (serving on COM6).
2. Robot panel → **Server**, host `0.0.0.0`, port `3000` → **Start**.
3. Point the UR3 program at `192.168.0.100:3000`; it connects (lamp green).
4. Click a piece, then its target square. The emulator moves it on screen and
   logs + sends the packet to the robot:

   ```
   SYS  move e2->e4
   UR3  -> sent packet (1.5.2.5.4.0) [MOVE]
   ```
   Pick **Promote to:** Q/R/B/N before moving a pawn to the last rank.

### Typical session — test chess_main (Client mode)

1. Start `chess_main.py` (brain): listens on `:3000`, opens COM5.
2. Board panel → com0com COM6 → **Start**. Robot panel → **Client**,
   host `127.0.0.1`, port `3000` → **Start**.
3. Make a white move on the board → press **Send MOVE →**. The brain scans the
   board over COM, recognises your move, and sends the black reply, which the
   emulator applies and logs.

## Files

| File                   | Purpose                                                   |
|------------------------|-----------------------------------------------------------|
| `emulator.py`          | GUI app: board model, COM/TCP board servers, robot server/client |
| `com0com_setup.py`     | Finds `setupc.exe`, lists/creates the virtual COM pair     |
| `..\Chess\board_tcp_serial.py` | Drop-in `serial.Serial` over TCP (no-driver fallback) |
| `run_emulator.bat`     | Double-click launcher                                      |
| `requirements.txt`     | `pyserial` (+ com0com note)                                |
