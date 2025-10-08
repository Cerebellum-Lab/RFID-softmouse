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

    Emits events onto queue:
      {'tag': '<alnum string>', 'ts': <epoch float>}
    On fatal (startup) error emits:
      {'error': '<message>'}
    Later runtime errors emit one structured error dict then exit.

    Added verbose instrumentation:
      - Logs every read chunk
      - Logs every parsed line (raw, cleaned tag, formatting status, expected flag, reason)
      - Logs enqueue success/failure
    """
    def report_error(msg: str, exc: Exception | None = None, trace: bool = False):
        payload = {'error': msg}
        if exc is not None:
            payload['exc'] = repr(exc)
        if trace:
            payload['trace'] = traceback.format_exc(limit=12)
        try:
            q.put(payload)
        except Exception:
            pass
        log.error(msg, exc_info=trace)

    if serial is None:
        report_error('pyserial not installed')
        return

    log.info('Opening serial port attempt port=%s baud=%s', port, baud)
    try:
        ser = serial.Serial(port, baudrate=baud, timeout=0)
    except Exception as e:  # pragma: no cover
        report_error(f'open failed: {e}', e, trace=False)
        return
    else:
        try:
            log.info('Serial port open success port=%s baud=%s bytesize=%s parity=%s stopbits=%s',
                     port, baud, getattr(ser, 'bytesize', '?'), getattr(ser, 'parity', '?'), getattr(ser, 'stopbits', '?'))
        except Exception:
            pass

    buf = bytearray()
    MAX_LINE_LEN = 512  # safeguard against unbroken stream
    log.info('RFID listener started port=%s baud=%s', port, baud)

    # Dedup state
    last_tag: str | None = None
    last_tag_time: float = 0.0
    DEDUP_WINDOW = 1.0  # seconds

    TAG_LEN = 15  # expected ASCII alphanumeric length

    def analyze_line(raw_bytes: bytes) -> dict:
        """Parse raw line into fixed 15-char ASCII alphanumeric tag.

        - Keep only 0-9, A-Z, a-z characters (exclude extended bytes like \xf4 even if isalnum()).
        - Detect repeated concatenations of the same 15-char block.
        - Truncate longer sequences to first 15 chars.
        - Only 'expected' if we have exactly 15 ASCII alnum chars after cleaning.
        """
        stripped = raw_bytes.strip(b'\r\n\x00')
        ascii_alnum = ''.join(chr(c) for c in stripped if (48 <= c <= 57) or (65 <= c <= 90) or (97 <= c <= 122))
        reason_parts = []
        duplicate = False
        truncated = False
        if not ascii_alnum:
            reason_parts.append('no_alnum')
        if len(ascii_alnum) >= 2*TAG_LEN and ascii_alnum[:TAG_LEN] == ascii_alnum[TAG_LEN:2*TAG_LEN]:
            duplicate = True
            reason_parts.append('duplicate_block')
        if len(ascii_alnum) >= TAG_LEN:
            final_tag = ascii_alnum[:TAG_LEN]
            if len(ascii_alnum) > TAG_LEN:
                truncated = True
                reason_parts.append('truncated_extra')
        else:
            final_tag = ascii_alnum
            reason_parts.append('too_short')
        if not reason_parts:
            reason_parts.append('ok')
        expected = (len(final_tag) == TAG_LEN)
        return {
            'raw': raw_bytes,
            'stripped': stripped,
            'ascii_alnum': ascii_alnum,
            'final_tag': final_tag,
            'expected': expected,
            'duplicate': duplicate,
            'truncated': truncated,
            'reason': '+'.join(reason_parts)
        }

    try:
        while not stop_event.is_set():
            try:
                chunk = ser.read(128)  # non‑blocking (timeout=0)
            except Exception as e:
                report_error(f'read failed: {e}', e, trace=True)
                break

            if chunk:
                log.debug('Read %d bytes: %r', len(chunk), chunk)
                buf.extend(chunk)

                # Prevent runaway buffer if no newline ever arrives
                if len(buf) > 10 * MAX_LINE_LEN:
                    log.warning('Shrinking runaway buffer size=%d', len(buf))
                    del buf[:-MAX_LINE_LEN]  # keep last segment

                # Immediate emit strategy: first look for newline-delimited frames; else emit mid-buffer when ready
                start = 0
                emitted = False
                for i, bch in enumerate(buf):
                    if bch in (10, 13):
                        frame = buf[start:i]
                        start = i + 1
                        if not frame.strip():
                            continue
                        analysis = analyze_line(frame)
                        if analysis['expected']:
                            now = time.time()
                            if last_tag == analysis['final_tag'] and (now - last_tag_time) < DEDUP_WINDOW:
                                log.debug('Duplicate tag suppressed tag=%s dt=%.3f', analysis['final_tag'], now - last_tag_time)
                            else:
                                try:
                                    q.put({'tag': analysis['final_tag'], 'ts': now})
                                    log.info('Tag emitted tag=%s (EOL)', analysis['final_tag'])
                                    last_tag = analysis['final_tag']
                                    last_tag_time = now
                                except Exception:
                                    log.exception('Failed enqueue tag=%r (EOL)', analysis['final_tag'])
                                emitted = True
                if start:
                    del buf[:start]
                if not emitted:
                    # Mid-buffer attempt
                    analysis_mid = analyze_line(bytes(buf))
                    if analysis_mid['expected']:
                        now = time.time()
                        if last_tag == analysis_mid['final_tag'] and (now - last_tag_time) < DEDUP_WINDOW:
                            log.debug('Duplicate tag suppressed tag=%s dt=%.3f (mid)', analysis_mid['final_tag'], now - last_tag_time)
                        else:
                            try:
                                q.put({'tag': analysis_mid['final_tag'], 'ts': now})
                                log.info('Tag emitted tag=%s (mid-buffer)', analysis_mid['final_tag'])
                                last_tag = analysis_mid['final_tag']
                                last_tag_time = now
                            except Exception:
                                log.exception('Failed enqueue tag=%r (mid)', analysis_mid['final_tag'])
                        buf.clear()
                else:
                    # Clear entire buffer after processing newline-delimited emission(s)
                    buf.clear()
            else:
                # Nothing read; sleep briefly to avoid busy loop
                time.sleep(poll_interval)
    except KeyboardInterrupt:  # pragma: no cover
        log.info('RFID listener interrupted (KeyboardInterrupt)')
    except Exception as e:  # pragma: no cover
        report_error(f'listener crashed: {e}', e, trace=True)
    finally:
        try:
            # Flush any final (newline-terminated) data before closing
            if buf:
                line = bytes(buf).strip()
                if line:
                    analysis = analyze_line(bytes(buf))
                    if analysis['expected']:
                        now = time.time()
                        if last_tag == analysis['final_tag'] and (now - last_tag_time) < DEDUP_WINDOW:
                            log.debug('Duplicate tag suppressed at flush tag=%s dt=%.3f', analysis['final_tag'], now - last_tag_time)
                        else:
                            try:
                                q.put({'tag': analysis['final_tag'], 'ts': now, 'partial': True})
                                log.info('Flushed partial line raw=%r final_tag=%r reason=%s', analysis['raw'], analysis['final_tag'], analysis['reason'])
                                last_tag = analysis['final_tag']
                                last_tag_time = now
                            except Exception:
                                log.exception('Failed to enqueue partial final tag')
            ser.close()
        except Exception:
            pass
        log.info('RFID listener exiting')

__all__ = ['run_rfid_listener']
