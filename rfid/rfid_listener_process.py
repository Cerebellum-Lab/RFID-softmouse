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

    try:
        ser = serial.Serial(port, baudrate=baud, timeout=0)
    except Exception as e:  # pragma: no cover
        report_error(f'open failed: {e}', e, trace=False)
        return

    buf = bytearray()
    MAX_LINE_LEN = 512  # safeguard against unbroken stream
    log.info('RFID listener started port=%s baud=%s', port, baud)

    def analyze_line(raw_bytes: bytes) -> dict:
        """Return analysis dict about the raw line prior to queue emission."""
        stripped = raw_bytes.strip()
        tag = ''.join(chr(c) for c in stripped if chr(c).isalnum())
        all_alnum_original = all(chr(c).isalnum() for c in stripped)
        formatting_ok = (len(tag) == len(stripped) and all_alnum_original)
        if not tag:
            reason = 'no_alnum_chars'
        elif not formatting_ok:
            reason = 'removed_non_alnum'
        else:
            reason = 'ok'
        expected = bool(tag)  # Adjust this rule if a stricter expectation is required
        return {
            'raw': raw_bytes,
            'stripped': stripped,
            'tag': tag,
            'formatting_ok': formatting_ok,
            'expected': expected,
            'reason': reason,
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

                # Process complete lines (LF or CR)
                start = 0
                for i, bch in enumerate(buf):
                    if bch in (10, 13):  # \n or \r
                        line_bytes = buf[start:i]
                        start = i + 1
                        line = line_bytes.strip()
                        if not line:
                            log.debug('Discarding empty (whitespace) line raw=%r', line_bytes)
                            continue
                        if len(line) > MAX_LINE_LEN:
                            log.warning('Discarding overlong line (%d bytes) raw=%r', len(line), line_bytes)
                            continue

                        analysis = analyze_line(line_bytes)
                        log.info(
                            'Line received raw=%r stripped=%r tag=%r formatting_ok=%s expected=%s reason=%s',
                            analysis['raw'],
                            analysis['stripped'],
                            analysis['tag'],
                            analysis['formatting_ok'],
                            analysis['expected'],
                            analysis['reason'],
                        )

                        tag = analysis['tag']
                        if tag and analysis['expected']:
                            event = {'tag': tag, 'ts': time.time()}
                            try:
                                q.put(event)
                            except Exception:
                                log.exception('Failed to enqueue tag event tag=%r', tag)
                            else:
                                log.debug('Enqueued tag=%r', tag)
                        else:
                            log.debug('Tag not enqueued (expected=%s tag_present=%s)', analysis['expected'], bool(tag))
                # Keep remainder (partial line)
                if start:
                    del buf[:start]
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
                    analysis = {
                        'raw': bytes(buf),
                        'stripped': line,
                        'tag': ''.join(chr(c) for c in line if chr(c).isalnum())
                    }
                    if analysis['tag']:
                        try:
                            q.put({'tag': analysis['tag'], 'ts': time.time(), 'partial': True})
                            log.info('Flushed partial line raw=%r tag=%r', analysis['raw'], analysis['tag'])
                        except Exception:
                            log.exception('Failed to enqueue partial final tag')
            ser.close()
        except Exception:
            pass
        log.info('RFID listener exiting')

__all__ = ['run_rfid_listener']
