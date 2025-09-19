#!/usr/bin/env python3
"""Simple RFID serial listener.

Usage (Windows PowerShell examples):
  python rfid_serial_listener.py --port COM5
  python rfid_serial_listener.py --auto

Features:
- Auto-detect first likely USB/ACM/TTL serial if --auto used (falls back to listing all).
- Reads lines (LF/CR/LFCR tolerant) and attempts to extract RFID tokens.
- Prints each unique RFID once unless --repeat specified.
- Optional --raw flag to echo every raw line.
- Uses existing project logging (logs/app.log).

Assumptions:
- Reader sends each scan as a line containing only the tag OR the tag embedded in ASCII.
- RFID format: 8–16 alphanumeric (adjust with --min-len / --max-len).

Press Ctrl+C to exit.
"""
from __future__ import annotations
import argparse, re, sys, time
from typing import Optional, Set

try:
    import serial  # pyserial
    from serial.tools import list_ports
except ImportError:
    print("pyserial not installed. Install with: pip install pyserial", file=sys.stderr)
    sys.exit(1)

from app_logging import get_logger
log = get_logger('rfid_listener')

RFID_PATTERN_TEMPLATE = r"[0-9A-Fa-f]{MIN,MAX}"  # hex-like fallback


def detect_port() -> Optional[str]:
    """Return the first plausible serial port for an RFID USB TTL adapter."""
    ports = list(list_ports.comports())
    if not ports:
        return None
    # Heuristics: prefer USB / ACM / Serial keywords
    for p in ports:
        desc = f"{p.description} {p.hwid}".lower()
        if any(k in desc for k in ("usb", "ttl", "rfid", "cp210", "ch34", "ftdi", "acm")):
            return p.device
    # Fallback first
    return ports[0].device


def compile_pattern(min_len: int, max_len: int, custom: Optional[str]):
    if custom:
        return re.compile(custom)
    pat = RFID_PATTERN_TEMPLATE.replace('MIN', str(min_len)).replace('MAX', str(max_len))
    return re.compile(pat)


def extract_tokens(line: str, pattern: re.Pattern) -> Set[str]:
    return set(pattern.findall(line))


def open_serial(port: str, baud: int, timeout: float):
    return serial.Serial(port=port, baudrate=baud, timeout=timeout)


def main():
    ap = argparse.ArgumentParser(description="Listen on a serial port for RFID scans.")
    gsel = ap.add_mutually_exclusive_group(required=True)
    gsel.add_argument('--port', help='Explicit COM port (e.g. COM5 or /dev/ttyUSB0)')
    gsel.add_argument('--auto', action='store_true', help='Auto-detect first plausible port')
    ap.add_argument('--baud', type=int, default=9600, help='Baud rate (default 9600)')
    ap.add_argument('--timeout', type=float, default=0.2, help='Serial read timeout seconds (default 0.2)')
    ap.add_argument('--min-len', type=int, default=8, help='Minimum RFID length (default 8)')
    ap.add_argument('--max-len', type=int, default=16, help='Maximum RFID length (default 16)')
    ap.add_argument('--pattern', help='Custom regex pattern for RFID (overrides min/max).')
    ap.add_argument('--raw', action='store_true', help='Print every raw line received')
    ap.add_argument('--repeat', action='store_true', help='Print repeats (default prints each unique once)')
    ap.add_argument('--quiet', action='store_true', help='Suppress normal non-RFID logs to stdout')
    args = ap.parse_args()

    port = args.port
    if args.auto:
        port = detect_port()
        if not port:
            print('No serial ports found.', file=sys.stderr)
            sys.exit(2)
        print(f'Auto-selected port: {port}')

    pattern = compile_pattern(args.min_len, args.max_len, args.pattern)
    log.info('Starting RFID listener port=%s baud=%s', port, args.baud)

    try:
        ser = open_serial(port, args.baud, args.timeout)
    except Exception as e:
        log.exception('Failed to open serial port %s: %s', port, e)
        print(f'Failed to open serial port {port}: {e}', file=sys.stderr)
        sys.exit(3)

    seen: Set[str] = set()
    last_line_time = time.time()
    try:
        while True:
            try:
                raw = ser.readline()  # reads until \n or timeout
                if not raw:
                    # Periodic heartbeat every 10s
                    if not args.quiet and (time.time() - last_line_time) > 10:
                        print('[waiting for data...]')
                        last_line_time = time.time()
                    continue
                last_line_time = time.time()
                try:
                    line = raw.decode('utf-8', errors='ignore').strip('\r\n')
                except Exception:
                    line = repr(raw)

                if args.raw:
                    print(f'RAW: {line}')

                tokens = extract_tokens(line, pattern)
                if not tokens:
                    continue
                for t in tokens:
                    if not args.repeat and t in seen:
                        continue
                    seen.add(t)
                    ts = time.strftime('%Y-%m-%d %H:%M:%S')
                    print(f'[{ts}] RFID: {t}')
                    log.info('RFID scanned %s', t)
            except KeyboardInterrupt:
                print('\nInterrupted by user.')
                break
            except Exception as e:
                log.warning('Read loop error: %s', e)
                time.sleep(0.5)
    finally:
        try:
            ser.close()
        except Exception:
            pass
        log.info('RFID listener exiting')

if __name__ == '__main__':
    main()
