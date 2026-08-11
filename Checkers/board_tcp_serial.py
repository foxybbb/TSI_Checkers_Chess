# -*- coding: utf-8 -*-
"""
board_tcp_serial.TcpSerial — drop-in replacement for serial.Serial that talks to
the checkers board EMULATOR over TCP instead of a real COM port. Lets the game
run with no virtual-serial driver: the emulator hosts a TCP board server, the
game connects to it.

Enable via env var before launching the game:
    set BOARD_TCP=127.0.0.1:5006
    python main.py
If BOARD_TCP is unset, the game uses the real serial COM port.
"""

import socket


class TcpSerial:
    def __init__(self, target: str, baudrate: int = 9600, timeout: float = 2):
        host, _, port = target.partition(":")
        self.timeout = timeout
        self._buf = b""
        self.is_open = False
        self.sock = socket.create_connection((host, int(port)), timeout=timeout)
        self.sock.settimeout(timeout)
        self.is_open = True

    def write(self, data: bytes) -> int:
        self.sock.sendall(data)
        return len(data)

    def flush(self):
        pass

    def reset_input_buffer(self):
        self._buf = b""

    def reset_output_buffer(self):
        pass

    def readline(self) -> bytes:
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

    def close(self):
        self.is_open = False
        try:
            self.sock.close()
        except Exception:
            pass


def open_board(serial_port: str, baud: int, timeout: float = 2):
    """TcpSerial if BOARD_TCP is set, else a real serial.Serial on `serial_port`."""
    import os
    target = os.environ.get("BOARD_TCP")
    if target:
        print(f"[Board] BOARD_TCP set -> using TCP board bridge {target}")
        return TcpSerial(target, baud, timeout)
    import serial
    return serial.Serial(serial_port, baud, timeout=timeout)
