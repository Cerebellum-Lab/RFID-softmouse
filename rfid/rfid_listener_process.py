"""RFID listener background process.

Continuously reads lines from a serial port and emits cleaned tag
strings via a multiprocessing queue. Designed to be started/stopped
by the GUI without blocking.
"""
from __future__ import annotations
import multiprocessing as mp, time, traceback, sys, pathlib
from typing import Optional

try:
    import serial  # type: ignore
except Exception:  # pragma: no cover
    serial = None  # type: ignore

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from app_logging import get_logger  # type: ignore
log = get_logger('rfid.listener')


def run_rfid_listener(port: str, baud: int, q, stop_event, poll_interval: float = 0.05):
    """Process entry point.

    Writes dict events onto queue: {'tag': '...','ts': float}.
    On fatal error writes {'error': '...'} once then exits.
    """
    if serial is None:
        q.put({'error': 'pyserial not installed'})
        return
    try:
        ser = serial.Serial(port, baudrate=baud, timeout=0)
    except Exception as e:  # pragma: no cover
        q.put({'error': f'open failed: {e}'})
        return
    buf = b''
    log.info('RFID listener started port=%s baud=%s', port, baud)
    try:
        while not stop_event.is_set():
            try:
                chunk = ser.read(64)
            except Exception as e:
                q.put({'error': f'read failed: {e}'})
                break
            if chunk:
                buf += chunk
                # Split on newline or carriage returns
                while b'\n' in buf or b'\r' in buf:
                    for sep in (b'\n', b'\r'):
                        if sep in buf:
                            line, buf = buf.split(sep, 1)
                            break
                    line = line.strip()
                    if not line:
                        continue
                    tag = ''.join(chr(c) for c in line if chr(c).isalnum())
                    if tag:
                        q.put({'tag': tag, 'ts': time.time()})
            else:
                time.sleep(poll_interval)
    except KeyboardInterrupt:  # pragma: no cover
        pass
    except Exception as e:  # pragma: no cover
        q.put({'error': f'listener error: {e}', 'trace': traceback.format_exc(limit=6)})
    finally:
        try:
            ser.close()
        except Exception:
            pass
        log.info('RFID listener exiting')

__all__ = ['run_rfid_listener']
