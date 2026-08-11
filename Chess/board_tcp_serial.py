# -*- coding: utf-8 -*-
"""
board_tcp_serial.TcpSerial
==========================

A tiny drop-in replacement for `serial.Serial` that talks to the chess board
EMULATOR over TCP instead of a real COM port.  It implements only the handful
of methods `ArduinoHandler` in chess_main.py actually uses, so the rest of the
game does not change.

This lets you test the whole game on Windows with NO virtual-serial driver and
NO com0com: the emulator hosts a board server, chess_main connects to it.

Enable it without touching the default behaviour by setting an env var before
launching the game, e.g.:

    set BOARD_TCP=127.0.0.1:5005
    python chess_main.py

If BOARD_TCP is not set, chess_main keeps using the real serial COM port.
"""

import socket


class TcpSerial:
    """Quacks like serial.Serial for ArduinoHandler's needs (board scan)."""

    def __init__(self, target: str, baudrate: int = 9600, timeout: float = 2):
        host, _, port = target.partition(":")
        self.timeout = timeout
        self._buf = b""
        self.is_open = False
        self.sock = socket.create_connection((host, int(port)), timeout=timeout)
        self.sock.settimeout(timeout)
        self.is_open = True

    # -- write side --------------------------------------------------------- #
    def write(self, data: bytes) -> int:
        self.sock.sendall(data)
        return len(data)

    def flush(self) -> None:
        pass

    def reset_input_buffer(self) -> None:
        self._buf = b""

    def reset_output_buffer(self) -> None:
        pass

    # -- read side ---------------------------------------------------------- #
    def readline(self) -> bytes:
        """Read until newline or timeout — matches serial.Serial.readline()."""
        while b"\n" not in self._buf:
            try:
                chunk = self.sock.recv(256)
            except socket.timeout:
                break
            if not chunk:
                break
            self._buf += chunk
        if b"\n" in self._buf:
            line, self._buf = self._buf.split(b"\n", 1)
            return line + b"\n"
        line, self._buf = self._buf, b""
        return line

    def read(self, n: int = 1) -> bytes:
        try:
            return self.sock.recv(n)
        except socket.timeout:
            return b""

    # -- lifecycle ---------------------------------------------------------- #
    def close(self) -> None:
        self.is_open = False
        try:
            self.sock.close()
        except Exception:
            pass


def open_board(serial_port: str, baud: int, timeout: float = 2):
    """
    Factory used by chess_main: returns a TcpSerial if BOARD_TCP is set,
    otherwise a real serial.Serial on `serial_port`.
    """
    import os
    target = os.environ.get("BOARD_TCP")
    if target:
        print(f"[Arduino] BOARD_TCP set -> using TCP board bridge {target}")
        return TcpSerial(target, baud, timeout)
    import serial
    return serial.Serial(serial_port, baud, timeout=timeout)
