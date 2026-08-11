# -*- coding: utf-8 -*-
"""
com0com helper
==============

Finds com0com's `setupc.exe`, lists existing virtual port pairs, and (on
request) creates a COM5<->COM6 pair so the emulator can act as the Arduino on
one end while chess_main opens the other end as a normal COM port.

com0com is the standard free virtual null-modem driver for Windows:
    https://com0com.sourceforge.net/

Creating a pair needs administrator rights (driver operation); listing does not.
Everything here degrades gracefully and just reports status strings the GUI
shows, so the emulator still runs (TCP fallback) when com0com is absent.
"""

import os
import re
import subprocess

# Default pair we want: emulator serves on EMU_PORT, chess_main opens BRAIN_PORT
EMU_PORT_DEFAULT   = "COM6"
BRAIN_PORT_DEFAULT = "COM5"

_SEARCH_DIRS = [
    r"C:\Program Files (x86)\com0com",
    r"C:\Program Files\com0com",
]


def find_setupc():
    """Return full path to setupc.exe, or None if com0com isn't installed."""
    for d in _SEARCH_DIRS:
        p = os.path.join(d, "setupc.exe")
        if os.path.isfile(p):
            return p
    # Try the install location recorded in the registry.
    try:
        import winreg
        for root in (winreg.HKEY_LOCAL_MACHINE,):
            for sub in (r"SOFTWARE\com0com", r"SOFTWARE\WOW6432Node\com0com"):
                try:
                    with winreg.OpenKey(root, sub) as k:
                        base, _ = winreg.QueryValueEx(k, "Install_Dir")
                        p = os.path.join(base, "setupc.exe")
                        if os.path.isfile(p):
                            return p
                except OSError:
                    continue
    except Exception:
        pass
    return None


def _run(setupc, *args, timeout=20):
    """Run setupc with args; return (rc, combined_output)."""
    try:
        proc = subprocess.run(
            [setupc, *args],
            cwd=os.path.dirname(setupc),
            capture_output=True, text=True, timeout=timeout,
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except Exception as e:
        return -1, str(e)


def _run_elevated(setupc, *args, timeout=120):
    """
    Run setupc elevated via a UAC prompt (PowerShell Start-Process -Verb RunAs).
    Output can't be captured across the elevation boundary, so success is
    verified by the caller re-listing the pairs. Returns (launched_ok, message).
    """
    arglist = ",".join(f"'{a}'" for a in args)
    ps = (
        f"Start-Process -FilePath '{setupc}' "
        f"-ArgumentList {arglist} "
        f"-WorkingDirectory '{os.path.dirname(setupc)}' "
        f"-Verb RunAs -Wait"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, timeout=timeout,
        )
        if proc.returncode == 0:
            return True, "elevated setupc completed"
        err = (proc.stderr or proc.stdout or "").strip()
        # A cancelled UAC prompt shows up as an exception here.
        if "canceled" in err.lower() or "1223" in err:
            return False, "UAC prompt was cancelled"
        return False, err[:300] or "elevated setupc failed"
    except Exception as e:
        return False, str(e)


_REG_PARAMS = r"SYSTEM\CurrentControlSet\Services\com0com\Parameters"


def list_pairs(setupc=None):
    """
    Return [(cnc_name, port_name), ...] read from the REGISTRY (no admin needed).

    `port_name` is whatever com0com has configured: a real 'COMxx' name, or the
    literal 'COM#' placeholder (auto, not yet openable by Win32/pyserial).

    setupc's own `list` command requires elevation on recent builds, so we never
    rely on it for read-only queries.
    """
    pairs = []
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _REG_PARAMS) as root:
            i = 0
            while True:
                try:
                    name = winreg.EnumKey(root, i)
                except OSError:
                    break
                i += 1
                if not re.match(r"CNC[AB]\d+", name):
                    continue
                try:
                    with winreg.OpenKey(root, name) as k:
                        port, _ = winreg.QueryValueEx(k, "PortName")
                except OSError:
                    port = "COM#"
                pairs.append((name, port))
    except Exception:
        pass
    return pairs


def _index(cnc_name):
    m = re.search(r"\d+", cnc_name)
    return m.group(0) if m else ""


def find_existing_pair(setupc=None):
    """
    Return (com_a, com_b) for the first A/B pair that already has REAL, openable
    COMxx names (not the 'COM#' placeholder). Otherwise None.
    """
    pairs = list_pairs()
    by_idx = {}
    for name, port in pairs:
        by_idx.setdefault(_index(name), {})["A" if name.startswith("CNCA") else "B"] = port
    for idx, ab in by_idx.items():
        a, b = ab.get("A", ""), ab.get("B", "")
        if a.upper().startswith("COM") and a != "COM#" and \
           b.upper().startswith("COM") and b != "COM#":
            return a, b
    return None


def find_placeholder_pair():
    """Return (cncA, cncB, idx) for an existing pair whose names are 'COM#'."""
    by_idx = {}
    for name, port in list_pairs():
        by_idx.setdefault(_index(name), {})["A" if name.startswith("CNCA") else "B"] = (name, port)
    for idx, ab in by_idx.items():
        if "A" in ab and "B" in ab:
            (na, pa), (nb, pb) = ab["A"], ab["B"]
            if pa == "COM#" or pb == "COM#":
                return na, nb, idx
    return None


def _used_com_numbers():
    used = set()
    try:
        import serial.tools.list_ports as lp
        for p in lp.comports():
            m = re.fullmatch(r"COM(\d+)", p.device, re.I)
            if m:
                used.add(int(m.group(1)))
    except Exception:
        pass
    for _, port in list_pairs():
        m = re.fullmatch(r"COM(\d+)", port, re.I)
        if m:
            used.add(int(m.group(1)))
    return used


def pick_free_ports(count=2, start=20):
    used = _used_com_numbers()
    out, n = [], start
    while len(out) < count:
        if n not in used:
            out.append(f"COM{n}")
        n += 1
    return out


def ensure_pair(emu_port=EMU_PORT_DEFAULT, brain_port=BRAIN_PORT_DEFAULT):
    """
    Make sure a virtual COM pair exists.

    Returns dict:
        {'ok': bool, 'emu': <port>, 'brain': <port>, 'created': bool,
         'message': <str>, 'setupc': <path or None>}

    'emu'   = the port the EMULATOR opens (acts as Arduino)
    'brain' = the port chess_main should open (SERIAL_PORT)
    """
    setupc = find_setupc()
    if not setupc:
        return {
            "ok": False, "emu": None, "brain": None, "created": False,
            "setupc": None,
            "message": ("com0com not found. Install it from "
                        "https://com0com.sourceforge.net/ then click 'Setup com0com' "
                        "again, or use the TCP board bridge instead."),
        }

    # 1) Already have a usable COM/COM pair?  Use it, no elevation needed.
    existing = find_existing_pair()
    if existing:
        a, b = existing
        return _ok(a, b, setupc, created=False,
                   note="Using existing com0com pair")

    # 2) A pair exists but its ports are the 'COM#' placeholder -> just give them
    #    real names (elevated 'change'), which is cheaper than a fresh install.
    placeholder = find_placeholder_pair()
    if placeholder:
        na, nb, _ = placeholder
        brain_port, emu_port = pick_free_ports(2)
        ok, msg = _run_elevated(
            setupc,
            "--silent", "change", na, f"PortName={brain_port}",
        )
        _run_elevated(setupc, "--silent", "change", nb, f"PortName={emu_port}")
        again = find_existing_pair()
        if again:
            a, b = again
            return _ok(a, b, setupc, created=True,
                       note="Named existing com0com pair")
        return _fail(setupc,
                     f"Could not name the existing pair ({msg}). In com0com's "
                     f"'Setup Command Prompt' (Administrator) run:  "
                     f"change {na} PortName={brain_port} & change {nb} PortName={emu_port}")

    # 3) No pair at all -> create one with explicit free names (elevated).
    brain_port, emu_port = pick_free_ports(2)
    install_args = ("install", f"PortName={brain_port}", f"PortName={emu_port}")
    rc, out = _run(setupc, *install_args)
    if rc != 0:
        _run_elevated(setupc, *install_args)
    created = find_existing_pair()
    if created:
        a, b = created
        return _ok(a, b, setupc, created=True, note="Created com0com pair")
    return _fail(setupc,
                 f"Pair creation did not complete. In com0com's 'Setup Command "
                 f"Prompt' (Administrator) run:  install PortName={brain_port} "
                 f"PortName={emu_port}")


def _ok(brain, emu, setupc, created, note):
    return {
        "ok": True, "emu": emu, "brain": brain, "created": created, "setupc": setupc,
        "message": f"{note}: chess_main opens {brain}, emulator opens {emu}. "
                   f"Set chess_main SERIAL_PORT='{brain}'.",
    }


def _fail(setupc, message):
    return {"ok": False, "emu": None, "brain": None, "created": False,
            "setupc": setupc, "message": message}


if __name__ == "__main__":
    import json
    print("setupc:", find_setupc())
    print("pairs :", list_pairs())
    print(json.dumps(ensure_pair(), indent=2))
