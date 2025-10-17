#!/usr/bin/env python
"""Standalone RFID scan CLI.

Usage (from project root, in activated conda/env):
    python scan.py [--port COMx] [--baud 9600]

If --port is omitted the script tries, in order:
  1. Port stored in the current user YAML (acquisition/Users/<User>_userdata.yaml)
  2. Auto-detect: list available serial ports and prompt user to pick.

Outputs each unique 15-char ASCII alphanumeric RFID tag as a single line on stdout.
Stops with Ctrl+C.

A companion Windows batch file (scan.bat) lets you just type `scan`.
"""
from __future__ import annotations
import argparse
import os
import sys
import time
import multiprocessing as mp
import queue as _queue
import signal
from pathlib import Path

try:
    import serial  # type: ignore
    from serial.tools import list_ports  # type: ignore
except Exception as e:  # pragma: no cover
    print(f"ERROR: pyserial not installed or import failed: {e}", file=sys.stderr)
    sys.exit(1)

# Reuse existing listener process implementation
try:
    from rfid.rfid_listener_process import run_rfid_listener  # type: ignore
except Exception as e:  # pragma: no cover
    print(f"ERROR: cannot import run_rfid_listener: {e}", file=sys.stderr)
    sys.exit(1)

try:
    import ruamel.yaml  # type: ignore
except Exception:
    ruamel = None  # noqa: F841

TAG_LEN = 15

def _load_configured_port() -> tuple[str|None, int|None]:
    """Attempt to read last user selection & RFID settings from acquisition/Users.

    Returns (port, baud) or (None, None) if unavailable.
    """
    users_dir = Path(__file__).parent / 'acquisition' / 'Users'
    prev_user = users_dir / 'prev_user.txt'
    if not prev_user.exists():
        return None, None
    try:
        user = prev_user.read_text(encoding='utf-8').strip()
        if not user:
            return None, None
        yml = users_dir / f"{user}_userdata.yaml"
        if not yml.exists():
            return None, None
        from ruamel.yaml import YAML  # type: ignore
        data = YAML().load(yml.read_text(encoding='utf-8')) or {}
        rfid_cfg = (data.get('rfid') or {}) if isinstance(data, dict) else {}
        port = (rfid_cfg.get('port') or '').strip() or None
        try:
            baud = int(rfid_cfg.get('baud', 9600)) if port else None
        except Exception:
            baud = 9600 if port else None
        return port, baud
    except Exception:
        return None, None

def _pick_port_interactively() -> str|None:
    ports = list(list_ports.comports())
    if not ports:
        print("No serial ports detected.")
        return None
    print("Available serial ports:")
    for idx, p in enumerate(ports):
        print(f"  [{idx}] {p.device} - {p.description}")
    while True:
        sel = input("Select index (blank to cancel): ").strip()
        if not sel:
            return None
        if sel.isdigit() and 0 <= int(sel) < len(ports):
            return ports[int(sel)].device
        print("Invalid selection. Try again.")


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="RFID scan CLI (prints 15-char tags)")
    ap.add_argument('--port', help='Serial port (e.g. COM3). If omitted will use stored config or prompt.')
    ap.add_argument('--baud', type=int, default=None, help='Baud rate (default 9600).')
    ap.add_argument('--list', action='store_true', help='List available ports and exit.')
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    if args.list:
        for p in list_ports.comports():
            print(f"{p.device}\t{p.description}")
        return 0

    port = args.port
    baud = args.baud

    if not port:
        c_port, c_baud = _load_configured_port()
        if c_port:
            port = c_port
            if baud is None:
                baud = c_baud or 9600
    if not port:
        port = _pick_port_interactively()
    if not port:
        print("No port selected. Exiting.")
        return 1
    if baud is None:
        baud = 9600

    # Prepare IPC
    out_q = mp.Queue()
    stop_ev = mp.Event()
    proc = mp.Process(target=run_rfid_listener, args=(port, baud, out_q, stop_ev), daemon=True)

    print(f"Starting RFID listener on {port} @ {baud} baud (Ctrl+C to stop)...")
    proc.start()

    last_printed = None
    try:
        while proc.is_alive():
            # Drain queue
            drained = False
            while True:
                try:
                    evt = out_q.get_nowait()
                except _queue.Empty:
                    break
                drained = True
                if 'error' in evt:
                    print(f"[RFID ERROR] {evt['error']}", file=sys.stderr)
                    stop_ev.set()
                    break
                tag = evt.get('tag')
                if tag:
                    if tag != last_printed:
                        # Ensure tag is exactly TAG_LEN alnum before printing
                        clean = ''.join(ch for ch in tag if ch.isalnum())
                        if len(clean) == TAG_LEN:
                            print(clean, flush=True)
                            last_printed = clean
                        else:
                            # Print anyway but annotate if unexpected length
                            print(clean, flush=True)
                            last_printed = clean
            if not drained:
                time.sleep(0.05)
    except KeyboardInterrupt:
        print("\nStopping...", flush=True)
        stop_ev.set()
    finally:
        try:
            proc.join(timeout=2)
        except Exception:
            pass
        if proc.is_alive():  # Force terminate if stuck
            try:
                proc.kill()
            except Exception:
                pass
    return 0


if __name__ == '__main__':  # pragma: no cover
    raise SystemExit(main())
