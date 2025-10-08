"""Minimal RFID serial listener.

Reads raw serial data, logs it, extracts first 15-char alphanumeric tag,
deduplicates within a time window, sends to GUI via queue, then clears buffer.
"""
from __future__ import annotations
import time, pathlib, sys, re
import multiprocessing as mp  # type: ignore
try:
    import serial  # type: ignore
except Exception:
    serial = None  # type: ignore

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from app_logging import get_logger  # type: ignore
log = get_logger("rfid.listener")

TAG_LEN = 15
TAG_RE = re.compile(rb'([A-Za-z0-9]{15})')

def run_rfid_listener(
    port: str,
    baud: int,
    q,
    stop_event,
    poll_interval: float = 0.05,
    dedup_window: float = 1.0,
    read_size: int = 256,
    logger_name: str | None = None,
):
    if logger_name:
        global log
        log = get_logger(logger_name)
    if serial is None:
        log.error("pyserial not installed")
        return
    try:
        ser = serial.Serial(port, baudrate=baud, timeout=0)
    except Exception as e:
        log.error("open failed: %s", e)
        return
    log.info("RFID listener started port=%s baud=%s", port, baud)

    buf = bytearray()
    last_tag = None
    last_time = 0.0

    def emit(tag: str):
        nonlocal last_tag, last_time
        now = time.time()
        if tag == last_tag and (now - last_time) < dedup_window:
            return
        try:
            q.put({'tag': tag, 'ts': now})
        except Exception:
            pass
        last_tag = tag
        last_time = now
        log.info("Tag %s", tag)

    try:
        while not stop_event.is_set():
            try:
                chunk = ser.read(read_size)
            except Exception as e:
                log.error("read failed: %s", e)
                break
            if chunk:
                log.debug("raw %r", chunk)
                buf.extend(chunk)
                m = TAG_RE.search(buf)
                if m:
                    tag_bytes = m.group(1)
                    tag = tag_bytes.decode('ascii', errors='ignore')
                    if len(tag) == TAG_LEN:
                        emit(tag)
                        buf.clear()
                        continue
            else:
                time.sleep(poll_interval)
    except KeyboardInterrupt:
        pass
    except Exception as e:
        log.error("listener crashed: %s", e)
    finally:
        try:
            ser.close()
        except Exception:
            pass
        log.info("RFID listener exiting")

__all__ = ["run_rfid_listener"]
